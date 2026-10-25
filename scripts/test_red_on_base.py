#!/usr/bin/env python3
"""S4 harness: a harness class that only skips is not red on the base.

The plan check (scripts/test_plan_118.py harness_problems) used to accept a class
whose run printed NOT BUILT and ended in skips as "red on the base", while the
grader (scripts/loop/grade_build.py red_reason) reads a skip as green. The two
disagreed, so a sub unit could pass the plan check with a harness the grader then
refused. This harness pins one convention: red means a named assertion failed.

Written first, red on the base by assertion (1.1.8 stability release, owner ruling
C2, 2026-10-24; spec docs/plan/specs/1.1.9/S4.md on hub main).
Done check: python3 -B scripts/test_red_on_base.py SkipIsNotRed
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_plan_118 as T  # noqa: E402

SKIPPING = """import unittest


class Foo(unittest.TestCase):
    def test_x(self):
        self.skipTest("NOT BUILT: the function does not exist yet")


if __name__ == "__main__":
    unittest.main()
"""

FAILING = """import unittest


class Foo(unittest.TestCase):
    def test_x(self):
        self.assertEqual(1, 2, "the function is not built yet")


if __name__ == "__main__":
    unittest.main()
"""


class SkipIsNotRed(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="red-on-base-")
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(os.path.join(self.root, "docs", "plan"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_skipping_class_is_a_problem(self):
        self.write("scripts/test_fixture.py", SKIPPING)
        problems = T.harness_problems("scripts/test_fixture.py", ["Foo"], ["Foo"], self.root)
        self.assertEqual(len(problems), 1, "a class that only skips was accepted as red: %r" % problems)
        self.assertIn("skipped", problems[0])

    def test_an_assertion_red_is_accepted(self):
        self.write("scripts/test_fixture.py", FAILING)
        self.assertEqual(T.harness_problems("scripts/test_fixture.py", ["Foo"], ["Foo"], self.root), [])

    def test_a_file_with_no_classes_is_a_problem(self):
        self.write("scripts/test_fixture.py", "import unittest\n")
        problems = T.harness_problems("scripts/test_fixture.py", ["Foo"], ["Foo"], self.root)
        self.assertEqual(problems, ["scripts/test_fixture.py: class Foo missing"])

    def test_a_corrupt_file_is_a_problem(self):
        self.write("scripts/test_fixture.py", "class Foo(:\n")
        problems = T.harness_problems("scripts/test_fixture.py", ["Foo"], ["Foo"], self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn("does not parse", problems[0])

    def test_a_missing_harness_is_red(self):
        self.assertTrue(hasattr(T, "harness_case_problems"),
                        "test_plan_118 has no harness_case_problems: the group check still skips a harness "
                        "with no class written instead of failing it")
        plan = {"units": [{"id": "U1", "state": "OPEN",
                           "sub_done_checks": {"U1.a": "python3 -B scripts/test_missing.py Foo"}}]}
        self.write("docs/plan/FIXTURE-WBS.json", json.dumps(plan))
        skip, problems = T.harness_case_problems("G", {"G": ["scripts/test_missing.py"]},
                                                 "docs/plan/FIXTURE-WBS.json", root=self.root)
        self.assertIsNone(skip, "a harness group with no class written skipped: %r" % skip)
        self.assertEqual(problems, ["scripts/test_missing.py: absent"])

    def test_a_group_whose_units_are_all_deferred_owes_nothing(self):
        if not hasattr(T, "harness_case_problems"):
            self.fail("test_plan_118 has no harness_case_problems")
        plan = {"units": [{"id": "U1", "state": "DEFERRED",
                           "sub_done_checks": {"U1.a": "python3 -B scripts/test_missing.py Foo"}}]}
        self.write("docs/plan/FIXTURE-WBS.json", json.dumps(plan))
        skip, problems = T.harness_case_problems("G", {"G": ["scripts/test_missing.py"]},
                                                 "docs/plan/FIXTURE-WBS.json", root=self.root)
        self.assertIsNotNone(skip)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
