#!/usr/bin/env python3
"""LD1.a: every WITHHELD line the runner writes carries its cause and its remedy, and a grade that judged
nothing names BROTHER_CODE_ROOT when the launch has no frozen code root.

unit_runner.py is a script, not importable, so each helper is lifted out of its parsed source with the loader
scripts/loop/test_unit_runner_h3a.py uses (test_unit_runner_probe_gate.load_fn). A name the source does not
define lifts to None, which is a FAILURE here and never a skip, so every case below is red on the base. The end
to end cases drive the real runner through the harness of scripts/test_unit_runner_native.py, whose grade lane
is a stub: FAKE_GRADE_MODE=timeout kills the grade before it writes a verdict, which is exactly the park this
unit is about.

Run from the repository root: python3 -B scripts/loop/test_unit_runner_withheld_reason.py
"""
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

import test_unit_runner_probe_gate as PG  # noqa: E402

UNIT_RUNNER = os.path.join(HERE, "unit_runner.py")
ROOT_REMEDY = "set BROTHER_CODE_ROOT to the absolute path of the frozen code directory and relaunch the loop"
GRADER_REMEDY = ("read lane.log and grades/ in this round folder, fix the grader or machine fact it names; "
                 "the pool re-seats the sub unit when its spec or a judge tool changes")
NO_NOTE = ("no diagnosis note will arrive: the diagnostician is skipped because BROTHER_CODE_ROOT is not set in this launch")


def _lift(name):
    """The named function of unit_runner.py, lifted from the parsed source, or None when the source has no such
    name. None is the base tree's answer and it is a failure in every case below, never a skip."""
    return PG.load_fn(UNIT_RUNNER, name)


def _source():
    with open(UNIT_RUNNER, "rb") as fh:
        return fh.read().decode("utf-8")


def _module_constant(name):
    """A module level constant of unit_runner.py, read from the parsed source so the case never depends on the
    loader's own treatment of non function names."""
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    return None


class TheGradeThatJudgedNothing(unittest.TestCase):

    def test_grade_nothing_line_keeps_the_prefix_and_carries_the_graders_words(self):
        fn = _lift("grade_nothing_line")
        self.assertTrue(callable(fn), "unit_runner.grade_nothing_line is missing")
        line = fn(0, 2, "step one\nstep two\nFAIL: the property it names is not implemented\n", "/tmp/out", True)
        self.assertTrue(line.startswith(
            "WITHHELD round 0: NO-DATA, the grade judged nothing (grade stage exit 2, no completed verdict; "
            "the grader said: FAIL: the property it names is not implemented) builds kept in /tmp/out;"), line)
        self.assertIn("the kept builds are not re-graded automatically", line)
        self.assertTrue(line.endswith("; remedy: " + GRADER_REMEDY), line)

    def test_a_missing_code_root_is_named_with_the_launch_variable_and_remedy(self):
        fn = _lift("grade_nothing_line")
        self.assertTrue(callable(fn), "unit_runner.grade_nothing_line is missing")
        line = fn(1, 3, "nothing here\n", "/tmp/x/out", False)
        self.assertIn(NO_NOTE, line)
        self.assertTrue(line.endswith("; remedy: " + ROOT_REMEDY), line)

    def test_a_set_code_root_gives_the_grader_remedy_and_no_diagnostician_clause(self):
        fn = _lift("grade_nothing_line")
        self.assertTrue(callable(fn), "unit_runner.grade_nothing_line is missing")
        line = fn(0, 2, "FAIL x\n", "/tmp/x/out", True)
        self.assertNotIn("no diagnosis note will arrive", line)
        self.assertNotIn("BROTHER_CODE_ROOT is not set", line)
        self.assertTrue(line.endswith("; remedy: " + GRADER_REMEDY), line)

    def test_the_line_is_one_bounded_line_whatever_the_grader_printed(self):
        one_line = _lift("one_line")
        gnl = _lift("grade_nothing_line")
        self.assertTrue(callable(one_line), "unit_runner.one_line is missing")
        self.assertTrue(callable(gnl), "unit_runner.grade_nothing_line is missing")
        self.assertEqual(one_line("a\nb\rc\x1bd", 100), "a b c d")
        self.assertEqual(one_line("  a   b  ", 100), "a b")
        self.assertEqual(one_line("abcdef", 3), "abc")
        self.assertEqual(one_line("x" * 5000, 300), "x" * 300)
        line = gnl(0, 2, "y" * 4000, "/tmp/" + "z" * 400 + "/out", False)
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertLess(len(line), 900)

    def test_with_remedy_appends_once_and_falls_back(self):
        fn = _lift("with_remedy")
        self.assertTrue(callable(fn), "unit_runner.with_remedy is missing")
        fallback = _module_constant("REMEDY_FALLBACK")
        self.assertTrue(fallback, "unit_runner.REMEDY_FALLBACK is missing")
        self.assertEqual(fn("cause", "fix it"), "cause; remedy: fix it")
        self.assertEqual(fn("cause; remedy: keep", "other"), "cause; remedy: keep")
        self.assertEqual(fn("cause", ""), "cause; remedy: " + fallback)
        self.assertEqual(fn("cause", "   "), "cause; remedy: " + fallback)
        self.assertEqual(fn("cause; remedy: keep", "other").count("remedy:"), 1)
        self.assertLessEqual(len(fn("z" * 5000, "y" * 5000)), 880)

    def test_non_string_inputs_never_raise_in_the_parking_path(self):
        grader_said = _lift("grader_said")
        with_remedy = _lift("with_remedy")
        gnl = _lift("grade_nothing_line")
        one_line = _lift("one_line")
        for name, fn in (("grader_said", grader_said), ("with_remedy", with_remedy),
                         ("grade_nothing_line", gnl), ("one_line", one_line)):
            self.assertTrue(callable(fn), "unit_runner.%s is missing" % name)
        hostile = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()]
        for v in hostile:
            self.assertIsInstance(grader_said(v), str)
            self.assertIsInstance(with_remedy(v, v), str)
            self.assertIsInstance(gnl(v, v, v, v, v), str)
            for lim in hostile:
                try:
                    self.assertIsInstance(one_line(v, lim), str)
                except ValueError:  # sbe: allow-silent the refusal is the outcome under test
                    pass

    def test_every_withheld_site_in_the_runner_goes_through_the_helpers(self):
        tree = ast.parse(_source())
        routed = 0
        bare = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "status" and node.args):
                continue
            first = node.args[0]
            if isinstance(first, ast.Call) and getattr(first.func, "id", "") in ("with_remedy", "grade_nothing_line"):
                routed += 1
                continue
            text = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                text = first.value
            elif (isinstance(first, ast.BinOp) and isinstance(first.op, ast.Mod)
                  and isinstance(first.left, ast.Constant) and isinstance(first.left.value, str)):
                text = first.left.value
            if text and text.startswith("WITHHELD"):
                bare.append(node.lineno)
        self.assertEqual(bare, [], "WITHHELD status lines not routed through the new helpers: lines %s" % bare)
        self.assertEqual(routed, 8, "expected 8 WITHHELD status calls routed through the helpers, found %d" % routed)


class EndToEnd(unittest.TestCase):
    """The real unit_runner.py bytes, the harness of scripts/test_unit_runner_native.py, and the stub grade lane.
    ONE condition per case: the code root variable, then the grade that judged nothing."""

    def test_a_grade_that_judged_nothing_parks_with_cause_and_remedy_end_to_end(self):
        import test_unit_runner_native as NAT
        r = NAT.Run(dict(NAT.ON, FAKE_GRADE_MODE="timeout", GRADE_TIMEOUT="2"), rounds=2)
        self.addCleanup(r.cleanup)
        status = r.status()
        self.assertTrue(status.startswith("WITHHELD round 0: NO-DATA, the grade judged nothing"), status)
        self.assertIn("the grader said:", status)
        self.assertIn("; remedy: ", status)
        self.assertFalse(os.path.exists(r.path("round1")), "a repair round was bought after a NO-DATA grade")

    def test_a_run_with_no_code_root_names_the_variable_end_to_end(self):
        import test_runner_straggler_settles as TRS
        os.makedirs(TRS.SCRATCH, exist_ok=True)
        # the frozen code root exists (the stub answers it from the working directory), but the launch never put
        # BROTHER_CODE_ROOT into the environment, so the grade site must read it as unset
        router = ("import os\nPUBLIC = 'public'\nclass Refused(Exception): pass\n"
                  "def registry(): return {'deepseek': {'transport': 'bridge'}}\n"
                  "def chain(kind, sens, pin=None, registry_arg=None): return ['deepseek']\n"
                  "def code_root(): return os.getcwd()\n")
        real = TRS.STUBS["model_router.py"]
        TRS.STUBS["model_router.py"] = router
        try:
            s = TRS.Scenario("finishes", env_extra={"FAKE_GRADE_MODE": "timeout", "GRADE_TIMEOUT": "2"},
                             drop=("BROTHER_CODE_ROOT",))
        finally:
            TRS.STUBS["model_router.py"] = real
        self.addCleanup(s.cleanup)
        status = s.status()
        self.assertTrue(status.startswith("WITHHELD round 0: NO-DATA, the grade judged nothing"), status)
        self.assertIn(NO_NOTE, status)
        self.assertIn(ROOT_REMEDY, status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
