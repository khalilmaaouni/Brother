#!/usr/bin/env python3
"""CT1.c: native_worker.profile_problems reads the shipped sandbox profile statically (L26).

The landing gate cannot start the 34 cases of test_native_worker.py that open a real sandbox (a sandbox cannot nest
inside another), so the profile itself gets a reader that never starts anything: one sentence per defect, and an
empty list for the profile both lanes ship. No seat path calls it, so a defect in the reader can never stop a session.

Run from the repository root: python3 -B scripts/loop/test_native_worker_profile_static.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import native_worker as NW  # noqa: E402  the module under test, edited by this build

NOT_TEXT = "the profile is not text"
SOUND = "(version 1)\n(allow default)\n(deny file-write*)\n"


class TheShippedProfiles(unittest.TestCase):
    """REQ-CT1-C-01 and REQ-CT1-C-06: both lanes read sound, and the names are the ones the head passes."""

    def test_the_shipped_profile_of_both_lanes_reads_sound(self):
        self.assertEqual(NW.profile_problems(NW.profile(False)), [])
        self.assertEqual(NW.profile_problems(NW.profile(True)), [])
        self.assertNotEqual(NW.profile(False), NW.profile(True), "the two lanes really are two profiles")
        self.assertEqual(NW.profile_problems(SOUND), [])

    def test_the_parameter_names_are_the_ones_the_head_passes(self):
        head = NW._sandbox_head("/tmp/ct1c-profile-probe", "", {}, False)
        passed = tuple(head[k + 1].split("=", 1)[0] for k, a in enumerate(head) if a == "-D" and k + 1 < len(head))
        self.assertEqual(passed, tuple(NW.SANDBOX_PARAMS))
        self.assertEqual(passed, ("WT", "TMP", "HOME", "TOOLDIR", "COMMON"))


class BrokenProfiles(unittest.TestCase):
    """REQ-CT1-C-02 to REQ-CT1-C-05: one defect per case, named exactly once, and never a raise."""

    def test_unbalanced_unterminated_and_wrong_version_are_named(self):
        self.assertEqual(NW.profile_problems("(version 1)\n(allow default))\n"), ["parentheses are unbalanced"])
        self.assertEqual(NW.profile_problems("(version 1)\n(allow default\n"), ["parentheses are unbalanced"])
        self.assertEqual(NW.profile_problems('(version 1)\n(allow default)\n"oops'), ["a string literal is not closed"])
        self.assertEqual(NW.profile_problems("(allow default)\n(deny file-write*)\n"), ["the first form is not (version 1)"])
        self.assertEqual(NW.profile_problems("(version 2)\n"), ["the first form is not (version 1)"])
        self.assertEqual(NW.profile_problems("version 1\n"), ["the first form is not (version 1)"])

    def test_an_unknown_parameter_and_an_odd_top_level_form_are_named(self):
        self.assertEqual(NW.profile_problems('(version 1)\n(allow file-read* (subpath (param "NOTHERE")))\n'),
                         ["unknown parameter NOTHERE"])
        self.assertEqual(NW.profile_problems("(version 1)\n(permute default)\n"),
                         ["a top level form is not allow or deny or version"])
        self.assertEqual(NW.profile_problems('(version 1)\n(permute default)\n(deny file-read* (subpath (param "NOPE")))\n'),
                         ["unknown parameter NOPE", "a top level form is not allow or deny or version"])
        self.assertEqual(NW.profile_problems(
            '(version 1)\n(deny file-read* (subpath (param "EXTRA")) (subpath (param "EXTRA")))\n', params=("EXTRA",)), [])
        self.assertEqual(NW.profile_problems(
            '(version 1)\n(deny file-read* (subpath (param "X")) (literal (param "X")))\n'), ["unknown parameter X"])

    def test_parentheses_inside_literals_do_not_count(self):
        self.assertEqual(NW.profile_problems('(version 1)\n(deny file-read* (regex #"^/a(b)c[)]$"))\n'), [])
        self.assertEqual(NW.profile_problems('(version 1)\n(deny file-read* (literal "/a(b)c)"))\n'), [])
        self.assertEqual(NW.profile_problems('(version 1)\n(deny file-read* (literal "/a\\"b(c)"))\n'), [])
        self.assertEqual(NW.profile_problems("(version 1)\n(deny file-read* (literal x) (y\n"),
                         ["parentheses are unbalanced"])

    def test_non_text_input_answers_one_sentence(self):
        for bad in (None, 0, 1, True, False, -1, 1.5, float("nan"), float("inf"), b"(version 1)",
                    [], ["(version 1)"], {}, {"version": 1}, (), (1, 2), {1, 2}, object()):
            self.assertEqual(NW.profile_problems(bad), [NOT_TEXT], repr(bad))
        for blank in ("", "   ", "\n\t ", "\r\n"):
            self.assertEqual(NW.profile_problems(blank), [NOT_TEXT])


if __name__ == "__main__":
    unittest.main()
