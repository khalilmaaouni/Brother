#!/usr/bin/env python3
"""The grade limits the frozen proof pair actually runs under (2026-10-05, owner ruling proofs-unlandable.json option A).

WHY. MG1.e and PR1.c grades hit "FAIL stage timed out after 1800s" at load 2 to 3. Measured alone that day: one run of
PR1.c's suite took 244 s, a passing PR1.c grade runs it about nine times (about 2450 s), and its original green read
"exit 124: timeout" against the grader's 300 s per command limit. The pair carries no GRADE_TIMEOUT (launch-env.sh and
proof_pair.sh set none), so the code defaults ARE the pair's limits: a knob raised only in an environment the pair never
freezes changes nothing.

HOW. Read from the sources (the runner is never imported: it starts work at import), one condition per case.

Run: python3 -B scripts/test_grade_limits.py
"""
import ast
import os
import re
import unittest

LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop")
MEASURED_PASSING_GRADE_S = 2450   # estimate: nine runs of PR1.c's suite, from the measured stages of 2026-10-05
MEASURED_SUITE_RUN_S = 244        # one green run of test_precut_review.py, alone, 2026-10-05


def read(name):
    with open(os.path.join(LOOP, name), encoding="utf-8") as fh:
        return fh.read()


def constant(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("%s is not a module constant" % name)


class TheGradeLimitsFitTheMeasuredNeed(unittest.TestCase):
    def test_the_grade_stage_limit_covers_a_passing_grade_with_margin(self):
        self.assertGreaterEqual(constant(read("unit_runner.py"), "GRADE_TIMEOUT_S"), MEASURED_PASSING_GRADE_S * 1.4)

    def test_every_grade_site_reads_the_one_limit(self):
        src = read("unit_runner.py")
        sites = re.findall(r'"grade_lane\.sh"\), wd, sub\], [^\n]*', src)
        self.assertEqual(len(sites), 2, sites)
        for line in sites:
            self.assertIn('B.knob("GRADE_TIMEOUT", GRADE_TIMEOUT_S)', line)

    def test_one_test_command_limit_covers_a_slow_suite_run(self):
        src = read("grade_build.py")
        limit = constant(src, "COMMAND_TIMEOUT_S")
        self.assertGreaterEqual(limit, MEASURED_SUITE_RUN_S * 2)
        self.assertLess(limit, constant(read("unit_runner.py"), "GRADE_TIMEOUT_S"))
        self.assertIn("timeout=COMMAND_TIMEOUT_S)", src)

    def test_the_pair_sets_no_grade_limit_of_its_own(self):
        # the default is what the pair runs; a value set here would bypass the measured one unseen
        self.assertNotIn("GRADE_TIMEOUT", read("proof_pair.sh"))
        self.assertNotIn("GRADE_TIMEOUT", read("loop_intake.py"))


if __name__ == "__main__":
    unittest.main()
