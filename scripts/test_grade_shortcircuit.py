#!/usr/bin/env python3
"""A build whose tests pass WITHOUT its code must not pay for a mutation suite whose result is already known.

WHAT IT PROTECTS, measured 2026-09-21 over 25.25 machine hours. The grade stage averaged 195 s and failed 102
of 166 builds; 25 of 51 sampled failures were exactly RED-WITHOUT-CODE, the grader removing the builder's
implementation, re-running the builder's own test, and finding it STILL PASSES. Every one of those then paid
for its full mutation suite, and a mutation is a FULL COPYTREE of the patched sandbox plus a test run.

THE ARGUMENT IS ABOUT MEANING BEFORE COST. A mutation asks "do these tests notice a change to the code". If the
tests did not notice the code being REMOVED ENTIRELY, they cannot notice a mutation of it. The result is known
in advance, so running it answers nothing.

It also removed a MISLEADING SECOND REASON. The old verdict read "FAIL: tests pass without the code; mutations:
5 applied, 0 caught", and that second clause would send a repair round chasing mutation counts when the real
defect is a test that cannot fail. The new verdict names one cause.

Controlled A/B on one synthetic build with 5 mutations: 21 s to 7 s, identical verdict.

GREEN-WITH-CODE is deliberately STILL RUN when the tests are vacuous: it is one test run rather than N tree
copies, and it tells the repair round whether the implementation itself works, which is the evidence that makes
the next round different."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# THE VERSIONED COPY FIRST. Reading ~/.claude/bin/grade_build.py made this test depend on a home folder the
# PUBLIC export tree does not have, and the pre-push hermetic gate refused it: green here, red where the public
# runner runs it. That is the exact class the gate exists to catch, and the second time it caught me today.
VERSIONED = os.path.join(HERE, "loop", "grade_build.py")
LIVE = os.path.expanduser("~/.claude/bin/grade_build.py")


def source():
    for path in (VERSIONED, LIVE):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            continue
    raise unittest.SkipTest("no grader source reachable from here")


class TheMutationSuiteIsSkippedWhenTheTestsAreVacuous(unittest.TestCase):
    def setUp(self):
        self.src = source()

    def test_the_skip_exists_and_is_keyed_on_red_without_code(self):
        self.assertIn("if not red and mutations:", self.src,
                      "the mutation loop must be skipped when the tests pass without the code")

    def test_the_skip_says_why_rather_than_going_quiet(self):
        """A silent skip is indistinguishable from a suite that ran and found nothing."""
        self.assertIn("MUTATIONS          SKIPPED", self.src)
        self.assertIn("cannot catch a mutation OF it", self.src)

    def test_green_with_code_still_runs(self):
        """It is ONE run, not N tree copies, and it is the evidence the next round needs."""
        i_red = self.src.index("RED-WITHOUT-CODE")
        i_green = self.src.index("GREEN-WITH-CODE")
        i_mut = self.src.index("if not red and mutations:")
        self.assertLess(i_red, i_green, "red without code is checked first")
        self.assertLess(i_green, i_mut, "green with code runs BEFORE the mutation skip, so it is never skipped")

    def test_a_vacuous_build_is_not_also_blamed_for_its_mutations(self):
        """The misleading second reason is gone: a vacuous test fails for ONE stated cause."""
        self.assertIn("if red and green == 0 and (applied < MIN_MUTATIONS or caught < applied):", self.src,
                      "the mutation complaint must be gated on the tests having gone red at all")

    def test_a_build_that_DOES_go_red_still_gets_its_full_mutation_suite(self):
        """The saving must not become a hole: a healthy build is graded exactly as before."""
        m = re.search(r"for m in mutations:", self.src)
        self.assertIsNotNone(m, "the mutation loop still exists for builds that went red")
        self.assertIn("mutations = build.get(\"mutations\") or []", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
