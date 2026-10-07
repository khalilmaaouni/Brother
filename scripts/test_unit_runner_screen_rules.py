#!/usr/bin/env python3
"""FX-09.6: the runner's hand typed safety screen sentence steps aside for the generated block when the switch is on.

WHY: unit_runner.py appended a hand typed statement of the grader's screen to every brief. It had drifted (it forbade
shutil, which the grader allows) and never named the rules that refused most builds. With BROTHER_BRIEF_SCREEN=on the
brief carries THE GRADER'S SCREEN, generated from grade_build.py, and the runner's sentence becomes a pointer to it;
off (the code default) the rules text is byte identical to today's. unit_runner.py works at import (it reads argv and
opens the live plan), so the constants are lifted by source, the same honest caveat as
test_build_brief_corrections._load, and the one use site is pinned so deleting the wiring cannot stay green.
Run: python3 -B scripts/test_unit_runner_screen_rules.py"""
import os
import unittest

UNIT_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "unit_runner.py")

#: today's RULES, copied from unit_runner.py at the FX-09 base commit: the golden the switch off must reproduce
GOLDEN = (
          '\nTHE SAFETY SCREEN (the grader refuses the whole build): no file you write imports subprocess, urllib, socket,'
          ' http, requests or shutil, and none calls run(), getattr() or exec() with a dynamic name, EXCEPT the paths thi'
          "s unit's plan names as command runners ({RUNNERS}) and there only with an argv whose EVERY element is a string"
          ' constant (a computed path or value goes through cwd= or env=, never into argv; see RULE 10). A test file neve'
          'r imports subprocess. Your tests must FAIL without your code and PASS with it: a test that passes on the uncha'
          'nged tree is refused.\n\nRules the machines enforce: every suite also runs in an EXPORT copy of the repository w'
          'ith an empty HOME and WITHOUT docs/plan, data files outside the code, or any private file, so a test builds it'
          's own fixture in a temp folder; a test that must read a live repository document is decorated with unittest.sk'
          'ipUnless(os.path.isfile(PATH), reason) and an existing suite you touch keeps that property; the done_check sui'
          'te is RED without your code and GREEN with it, on Python 3.9 AND 3.13; every EXISTING suite of a module you ed'
          'it stays green; every mutation you name makes a NAMED test fail; hostile input (wrong type, None, NaN, a bool '
          'where a number belongs, bytes that are not utf-8, a directory or a missing file where a file belongs, an unhas'
          "hable key, a record altered after it was parsed) is REFUSED with the module's own deliberate error or refusal "
          'value, never a raw interpreter exception; files are read as BYTES; one validation where all callers route thro'
          'ugh. Return ONE complete, self contained JSON build applying to the tree you were shown.\n')
GOLDEN_HAND = (
          '\nTHE SAFETY SCREEN (the grader refuses the whole build): no file you write imports subprocess, urllib, socket,'
          ' http, requests or shutil, and none calls run(), getattr() or exec() with a dynamic name, EXCEPT the paths thi'
          "s unit's plan names as command runners ({RUNNERS}) and there only with an argv whose EVERY element is a string"
          ' constant (a computed path or value goes through cwd= or env=, never into argv; see RULE 10). A test file neve'
          'r imports subprocess. Your tests must FAIL without your code and PASS with it: a test that passes on the uncha'
          'nged tree is refused.\n')


def _lift():
    with open(UNIT_RUNNER, encoding="utf-8") as fh:
        src = fh.read()
    ns = {}
    exec(compile(src[src.index("SCREEN_HAND = ("):src.index("\ntry:\n    _screen_on = ")], "unit_runner_rules", "exec"), ns)
    return ns, src


class TheRunnerRulesFollowTheSwitch(unittest.TestCase):
    def setUp(self):
        self.ns, self.src = _lift()

    def test_rules_off_is_todays_text_and_on_names_the_block(self):
        self.assertEqual(self.ns["SCREEN_HAND"] + self.ns["RULES_REST"], GOLDEN)
        self.assertEqual(self.ns["SCREEN_HAND"], GOLDEN_HAND)
        self.assertEqual(self.ns["RULES"], GOLDEN)
        on = self.ns["RULES_SCREEN"]
        self.assertTrue(on.startswith(self.ns["SCREEN_POINTER"]))
        self.assertIn("THE GRADER'S SCREEN", on)
        self.assertNotIn("shutil", on)
        self.assertTrue(on.endswith(self.ns["RULES_REST"]))

    def test_the_pointer_keeps_the_units_own_runner_list(self):
        on = self.ns["RULES_SCREEN"]
        self.assertIn("{RUNNERS}", on)
        self.assertIn("Your tests must FAIL without your code and PASS with it", on)

    def test_the_switch_is_read_from_the_grader_and_used_once(self):
        self.assertIn("_screen_on = grade_build.brief_screen_mode() == \"on\"", self.src)
        self.assertEqual(self.src.count("(RULES_SCREEN if _screen_on else RULES).replace(\"{RUNNERS}\""), 1)
        self.assertIn("\nimport grade_build", self.src)


if __name__ == "__main__":
    unittest.main()
