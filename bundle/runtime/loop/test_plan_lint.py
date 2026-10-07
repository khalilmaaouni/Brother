#!/usr/bin/env python3
"""Tests for scripts/loop/plan_lint.py (FX-13.1): the shared screen, the mode, the CLI and the plan rules.

Every fixture is built in a temporary directory; no live document is read.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

import plan_lint  # noqa: E402  (the module under test, beside this file)
import ev_gate  # noqa: E402  (FX-13.4: the runner's pricing, read by rule EV)


OK_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class T(unittest.TestCase):\n"
    "    def test_x(self):\n"
    "        self.assertTrue(True)\n"
    "\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    unittest.main()\n"
)

LATE_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class T(unittest.TestCase):\n"
    "    def test_x(self):\n"
    "        self.assertTrue(True)\n"
    "\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    unittest.main()\n"
    "\n"
    "\n"
    "class Late(unittest.TestCase):\n"
    "    def test_y(self):\n"
    "        self.assertTrue(True)\n"
)

MATCH_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class T(unittest.TestCase):\n"
    "    def test_x(self):\n"
    "        match 1:\n"
    "            case 1:\n"
    "                self.assertTrue(True)\n"
    "\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    unittest.main()\n"
)

HELPER_FIRST_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class Helper(object):\n"
    "    pass\n"
    "\n"
    "\n"
    "class T(unittest.TestCase):\n"
    "    def test_x(self):\n"
    "        self.assertTrue(True)\n"
    "\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    unittest.main()\n"
)

BASE_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class Base(unittest.TestCase):\n"
    "    def setUp(self):\n"
    "        self.v = 1\n"
    "\n"
    "\n"
    "class T(Base):\n"
    "    def test_x(self):\n"
    "        self.assertEqual(self.v, 1)\n"
    "\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    unittest.main()\n"
)

BARE_MAIN_TEST = (
    "import unittest\n"
    "\n"
    "unittest.main()\n"
    "\n"
    "\n"
    "class Late(unittest.TestCase):\n"
    "    def test_y(self):\n"
    "        self.assertTrue(True)\n"
)

OPEN_UNIT = {
    "id": "U",
    "state": "OPEN",
    "done_check": "python3 -B scripts/test_a.py",
    "spec": "spec.md",
    "sub_units": ["U.1"],
    "evidence": "",
}


class ScreenTest(unittest.TestCase):
    """The closer's screen, moved into scripts/loop and shared with the lint."""

    def test_screen_allows_one_test_command(self):
        self.assertEqual(plan_lint.screen_done_check("python3 -B scripts/test_a.py"),
                         [["python3", "-B", "scripts/test_a.py"]])

    def test_screen_refuses_a_c_command(self):
        self.assertIsNone(plan_lint.screen_done_check("python3 -c 'import shutil'"))

    def test_screen_refuses_a_pipe_an_absolute_path_and_prose(self):
        for command in (None, 17, b"python3 x.py", "", "   ", "run the tests",
                        "python3 -B scripts/test_a.py | tee out.log",
                        "python3 /usr/bin/env", "python3 -B ../scripts/test_a.py"):
            self.assertIsNone(plan_lint.screen_done_check(command), repr(command))

    def test_screen_reads_a_chain_the_closer_runs(self):
        steps = plan_lint.screen_done_check("python3 -B scripts/test_a.py && python3 -B scripts/test_b.py")
        self.assertEqual(len(steps), 2)

    def test_close_unit_and_plan_lint_share_one_screen(self):
        import close_unit
        self.assertIs(close_unit.screen_done_check, plan_lint.screen_done_check)


class ModeTest(unittest.TestCase):
    def test_unset_is_report(self):
        self.assertEqual(plan_lint.lint_mode({}), ("report", ""))

    def test_report_is_report(self):
        self.assertEqual(plan_lint.lint_mode({"BROTHER_RUNFLOW_LINT": "report"}), ("report", ""))

    def test_block_mode_is_block(self):
        self.assertEqual(plan_lint.lint_mode({"BROTHER_RUNFLOW_LINT": "block"}), ("block", ""))

    def test_unknown_mode_is_block_with_a_note(self):
        mode, note = plan_lint.lint_mode({"BROTHER_RUNFLOW_LINT": "blok"})
        self.assertEqual(mode, "block")
        self.assertIn("NO-DATA", note)
        self.assertIn("BROTHER_RUNFLOW_LINT=blok", note)

    def test_a_non_mapping_env_is_refused(self):
        for env in (17, "report", ["report"], ("report",)):
            with self.assertRaises(ValueError):
                plan_lint.lint_mode(env)

    def test_a_non_text_value_is_refused(self):
        with self.assertRaises(ValueError):
            plan_lint.lint_mode({"BROTHER_RUNFLOW_LINT": 17})


class _Fixture(unittest.TestCase):
    """A temporary repository root: the suite builds its own fixture and reads no live document."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def write_bytes(self, rel, blob):
        path = os.path.join(self.root, rel)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "wb") as fh:
            fh.write(blob)
        return path

    def plan(self, units):
        return self.write("plan.json", json.dumps({"units": units}))

    def lint(self, units, scope=".", **kw):
        return plan_lint.lint_plan(self.plan(units), root=self.root, scope=scope, **kw)

    def rules(self, report):
        return [(f["rule"], f["severity"]) for f in report["findings"]]

    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = plan_lint.main(argv)
        return (code, buf.getvalue())


class CliTest(_Fixture):
    def test_empty_plan_is_read_and_reports_zero(self):
        code, out = self.run_cli(["--report", self.plan([])])
        self.assertEqual(code, 0)
        self.assertIn("units 0 sections 0", out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("LINT SUMMARY"))

    def test_unreadable_plan_is_no_data_exit_3(self):
        code, out = self.run_cli(["--report", os.path.join(self.root, "not_there.json")])
        self.assertEqual(code, 3)
        self.assertTrue(out.strip().splitlines()[-1].startswith("LINT NO-DATA: the plan"))

    def test_a_plan_that_is_not_json_is_no_data(self):
        code, out = self.run_cli(["--report", self.write("plan.json", "{not json")])
        self.assertEqual(code, 3)
        self.assertIn("cannot be read", out)

    def test_a_plan_that_is_not_utf_8_is_no_data(self):
        code, out = self.run_cli(["--report", self.write_bytes("plan.json", b'\xff\xfe{"units": []}')])
        self.assertEqual(code, 3)

    def test_a_directory_where_the_plan_belongs_is_no_data(self):
        code, out = self.run_cli(["--report", self.root])
        self.assertEqual(code, 3)

    def test_units_that_are_not_a_list_is_no_data(self):
        code, out = self.run_cli(["--report", self.write("plan.json", json.dumps({"units": {}}))])
        self.assertEqual(code, 3)

    def test_bad_arguments_exit_2(self):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = plan_lint.main([])
        self.assertEqual(code, 2)
        self.assertIn("usage", err.getvalue().lower())

    def test_argparse_system_exit_is_returned_never_raised(self):
        """--help and a bad argument make argparse call sys.exit(); main returns that code to its caller."""
        for argv, expected in ((["--help"], 0), (["-h"], 0), ([], 2), (["--nope"], 2)):
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                code = plan_lint.main(argv)
            self.assertEqual(code, expected, argv)
        import close_unit
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            self.assertEqual(close_unit.main(["--help"]), 0)

    def test_one_clean_unit_has_no_finding(self):
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_a.py`\nThe suite is `scripts/test_a.py`.\n")
        code, out = self.run_cli(["--report", self.plan([OPEN_UNIT]), "--scope", "U"])
        self.assertEqual(code, 0)
        self.assertIn("blocking 0", out)
        self.assertIn("advisory 0", out)
        self.assertIn("no-data 0", out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("LINT SUMMARY"))


class RulesTest(_Fixture):
    def test_unit_without_sub_units_checks_only_its_own_check(self):
        self.write("scripts/test_a.py", OK_TEST)
        report = self.lint([{"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_a.py",
                             "sub_units": [], "evidence": ""}])
        self.assertEqual(self.rules(report), [])

    def test_unknown_state_is_linted_as_open(self):
        report = self.lint([{"id": "U", "state": "FLOATING", "done_check": "run the tests by hand",
                             "sub_units": [], "evidence": ""}])
        self.assertIn(("UNIT-CHECK", "BLOCKING"), self.rules(report))

    def test_old_l5c4_chained_unit_check_is_flagged(self):
        report = self.lint([{"id": "U", "state": "OPEN", "sub_units": [], "evidence": "",
                             "done_check": "python3 -B scripts/test_a.py && python3 -c 'import shutil'"}])
        self.assertIn(("UNIT-CHECK", "BLOCKING"), self.rules(report))

    def test_multi_line_fence_section_is_flagged(self):
        self.write("spec.md", "## U.1 first\nDone-check:\n```\npython3 a.py\n# a note\npython3 -c 'x'\n```\nThe suite is `scripts/test_a.py`.\n")
        report = self.lint([OPEN_UNIT])
        self.assertIn(("SECTION-CHECK", "BLOCKING"), self.rules(report))

    def test_a_section_without_a_backticked_path_is_flagged(self):
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", "## U.1 first\nDone-check:\n```\npython3 -B scripts/test_a.py\n```\n")
        report = self.lint([OPEN_UNIT])
        self.assertIn(("TOUCH", "BLOCKING"), self.rules(report))

    def test_class_after_unittest_main_is_flagged(self):
        self.write("scripts/test_late.py", LATE_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B -m unittest scripts.test_late.Late`\n"
                              "The suite is `scripts/test_late.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B -m unittest scripts.test_late.Late")])
        texts = [f["text"] for f in report["findings"] if f["rule"] == "TEST-FOUND"]
        self.assertTrue(any("Late" in t for t in texts), texts)
        self.assertFalse(any("no unittest.TestCase test method" in t for t in texts), texts)

    def test_unparseable_test_is_a_finding(self):
        self.write("scripts/test_broken.py", "def broken(:\n")
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_broken.py`\nThe suite is `scripts/test_broken.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_broken.py")])
        self.assertIn(("TEST-FOUND", "BLOCKING"), self.rules(report))

    def test_named_test_using_match_statement_is_flagged_for_39(self):
        self.write("scripts/test_match.py", MATCH_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_match.py`\nThe suite is `scripts/test_match.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_match.py")])
        self.assertIn(("TEST-FOUND", "BLOCKING"), self.rules(report))

    def test_absent_new_test_is_not_a_missing_test(self):
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_new.py`\nThe proof is `NEW: scripts/test_new.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_new.py")])
        self.assertEqual(self.rules(report), [])

    def test_landed_section_is_not_linted(self):
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_a.py`\n## U.2 second\nDone check: `python3 a.py`\nThe second touches `scripts/other.py`.\n")
        report = self.lint([{"id": "U", "state": "PARTIAL", "done_check": "python3 -B scripts/test_a.py",
                             "spec": "spec.md", "sub_units": ["U.1", "U.2"],
                             "evidence": "U.1 landed: the gate was green."}])
        subs = [f["sub"] for f in report["findings"]]
        self.assertNotIn("U.1", subs)
        self.assertIn("U.2", subs)

    def test_done_unit_is_not_an_admission_finding(self):
        unit = {"id": "D", "state": "DONE", "remains": "", "done_check": "run everything by hand",
                "evidence": "UNIT DONE 2026-01-01 00:00 : the check was re-run. `run everything by hand` printed: OK."}
        report = self.lint([unit])
        self.assertEqual(self.rules(report), [])

    def test_done_with_remains_not_built_is_flagged(self):
        unit = {"id": "J1", "state": "DONE", "remains": "NOT BUILT. a fresh council attack is in flight",
                "done_check": "python3 -B scripts/test_a.py",
                "evidence": "UNIT DONE 2026-01-01 00:00 : `python3 -B scripts/test_a.py` printed: OK."}
        report = self.lint([unit])
        self.assertIn(("DONE-INTEGRITY", "ADVISORY"), self.rules(report))

    def test_done_closure_line_rules_each_fire_alone(self):
        base = {"id": "D", "state": "DONE", "remains": "", "done_check": "python3 -B scripts/test_a.py"}
        for evidence in ("the unit was closed by hand",
                         "UNIT DONE 2026-01-01 00:00 : `python3 -B scripts/test_b.py` printed: OK.",
                         "UNIT DONE 2026-01-01 00:00 : `python3 -B scripts/test_a.py` printed: no result line"):
            report = self.lint([dict(base, evidence=evidence)])
            self.assertIn(("DONE-INTEGRITY", "ADVISORY"), self.rules(report), evidence)

    def test_a_plan_unit_that_is_not_a_record_is_no_data(self):
        report = self.lint(["not a record"])
        self.assertIn(("UNIT-CHECK", "NO-DATA"), self.rules(report))

    def test_a_non_text_root_is_no_data_not_a_crash(self):
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_a.py`\nThe suite is `scripts/test_a.py`.\n")
        report = plan_lint.lint_plan(self.plan([OPEN_UNIT]), root=None, scope=".")
        self.assertIn(("SPEC-FILE", "NO-DATA"), self.rules(report))
        self.assertIsInstance(plan_lint.lint_unit(OPEN_UNIT, {"units": []}, root=None), list)

    def test_a_helper_class_before_the_test_case_is_walked_past(self):
        """The class walk continues past a non TestCase class (mutation: stop at the first ClassDef)."""
        self.write("scripts/test_helper.py", HELPER_FIRST_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_helper.py`\nThe suite is `scripts/test_helper.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_helper.py")])
        self.assertEqual(self.rules(report), [])

    def test_a_test_case_through_an_intermediate_base_is_found(self):
        """Probe 2026-09-30: class T(Base) where Base(unittest.TestCase) is defined above it BLOCKED falsely."""
        self.write("scripts/test_base.py", BASE_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_base.py`\nThe suite is `scripts/test_base.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_base.py")])
        self.assertEqual(self.rules(report), [])

    def test_unittest_method_form_names_the_module_and_class_not_a_method_path(self):
        """Probe 2026-09-30: -m unittest scripts.test_a.T.test_x read as the path scripts/test_a/T/test_x.py."""
        self.assertEqual(plan_lint._test_targets("python3 -B -m unittest scripts.test_a.T.test_x"),
                         [("scripts" + os.sep + "test_a.py", "T")])
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B -m unittest scripts.test_a.T.test_x`\n"
                              "The suite is `scripts/test_a.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B -m unittest scripts.test_a.T.test_x")])
        self.assertEqual(self.rules(report), [])

    def test_a_bare_unittest_main_call_is_the_guard(self):
        """A class defined after a bare unittest.main() never runs (mutation: only the if guard counts)."""
        self.write("scripts/test_bare.py", BARE_MAIN_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_bare.py`\nThe suite is `scripts/test_bare.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_bare.py")])
        texts = [f["text"] for f in report["findings"] if f["rule"] == "TEST-FOUND"]
        self.assertTrue(any("no unittest.TestCase test method" in t for t in texts), texts)

    def test_an_absent_test_not_labelled_new_is_blocking(self):
        """The one fixture for OWN-E: the file is absent, nothing labels it NEW, the section is otherwise clean."""
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_gone.py`\nThe suite is `scripts/test_gone.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_gone.py")])
        texts = [f["text"] for f in report["findings"] if (f["rule"], f["severity"]) == ("TEST-FOUND", "BLOCKING")]
        self.assertTrue(any("not on disk" in t for t in texts), self.rules(report))

    def test_a_new_label_is_not_undone_by_a_later_unlabelled_mention(self):
        """FX-31.5, 2026-10-03: the Owns line labels the test NEW and a later mutation line names it bare; the bare
        mention overwrote the label and the absent NEW test read as missing (BLOCKING)."""
        self.write("spec.md", "## U.1 first\nOwns: `scripts/test_new.py` (NEW).\nDone check: `python3 -B scripts/test_new.py`\n"
                              "Mutation M-1 must fail `scripts/test_new.py`.\n")
        report = self.lint([dict(OPEN_UNIT, done_check="python3 -B scripts/test_new.py")])
        self.assertNotIn(("TEST-FOUND", "BLOCKING"), self.rules(report))

    def test_a_missing_spec_file_on_an_open_unit_is_blocking(self):
        """The one fixture for OWN-B: the unit check is clean, the spec path is simply not on disk."""
        self.write("scripts/test_a.py", OK_TEST)
        report = self.lint([OPEN_UNIT])
        self.assertEqual(self.rules(report), [("SPEC-FILE", "BLOCKING")])

    def test_an_open_inherited_red_the_unit_depends_on_is_blocking(self):
        """The one fixture for OWN-A: closed entries pass, an open one naming this unit's suite blocks."""
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_a.py`\nThe suite is `scripts/test_a.py`.\n")
        for status, expected in (("closed 2026-01-01", []), ("open", [("INHERITED-RED", "BLOCKING")])):
            plan = {"units": [OPEN_UNIT], "inherited_reds": [{"suite": "scripts/test_a.py", "status": status}]}
            report = plan_lint.lint_plan(self.write("plan.json", json.dumps(plan)), root=self.root, scope=".")
            self.assertEqual(self.rules(report), expected, status)

    def test_the_lint_reads_the_gate_rule_not_a_copy(self):
        """OWN-C was drift: the lint kept its own OK_CMD. Change the gate's rule and the lint must follow; an
        identity check is not enough, since re.compile hands the same object back for the same pattern."""
        import grade_build
        import re
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", "## U.1 first\nDone check: `python3 -B scripts/test_a.py`\nThe suite is `scripts/test_a.py`.\n")
        real = grade_build.OK_CMD
        grade_build.OK_CMD = re.compile(r"\A\Z")
        try:
            report = self.lint([OPEN_UNIT])
        finally:
            grade_build.OK_CMD = real
        self.assertIn(("SECTION-CHECK", "BLOCKING"), self.rules(report))
        self.assertEqual(self.rules(self.lint([OPEN_UNIT])), [])

    def test_the_lint_reads_the_store_landed_rule_not_a_copy(self):
        """OWN-D was drift: the lint kept its own sub_landed. Change the store's rule and the lint must follow."""
        import plan_store
        self.write("spec.md", "## U.1 first\nDone check: `python3 a.py`\n")
        real = plan_store.sub_landed
        plan_store.sub_landed = lambda sub, evidence: True
        try:
            report = self.lint([OPEN_UNIT])
        finally:
            plan_store.sub_landed = real
        self.assertNotIn("U.1", [f["sub"] for f in report["findings"]])
        self.assertIn("U.1", [f["sub"] for f in self.lint([OPEN_UNIT])["findings"]])

    def test_named_tests_are_parsed_as_python_3_9(self):
        """OWN-H: on a 3.9 interpreter the match fixture fails either way, so the 3.9 pin is read off the call."""
        seen = []
        real = plan_lint.ast.parse

        def spy(source, **kw):
            seen.append(kw.get("feature_version"))
            return real(source, **kw)
        plan_lint.ast.parse = spy
        try:
            plan_lint._parse_findings("U", "U.1", "scripts/test_a.py", OK_TEST, None)
        finally:
            plan_lint.ast.parse = real
        self.assertEqual(seen, [(3, 9)])

    def test_a_lint_line_never_carries_the_word_start(self):
        """OWN-I: loop_pass.sh counts lines matching START as started units, so a lint line never carries it."""
        line = plan_lint._line(plan_lint._finding("START-1", "START-1.1", "TOUCH", "BLOCKING", "the START row", "START over"))
        self.assertNotIn("START", line)
        self.assertTrue(line.startswith("LINT BLOCKING Start-1 Start-1.1 TOUCH:"), line)

    def test_hostile_inputs_are_refused_not_crashed(self):
        for unit in (None, 17, "U.1", [], ("U",), {"id": "U"}):
            self.assertIsInstance(plan_lint.lint_unit(unit, {"units": []}), list)
        lines, why = plan_lint.admission(None, None, None)
        self.assertIsInstance(lines, list)
        self.assertIsInstance(why, str)
        lines, why = plan_lint.admission({}, {"id": "U", "state": "OPEN", "done_check": None,
                                              "sub_units": "U.1", "evidence": None}, "U.1")
        self.assertIsInstance(lines, list)
        self.assertIsInstance(why, str)
        for value in (None, 17, b"`scripts/test_a.py`", [], {}):
            with self.assertRaises(ValueError):
                plan_lint.named_paths(value)
        for value in (None, 17, [b"x"], {}):
            with self.assertRaises(ValueError):
                plan_lint.labelled_paths(value)
        for env in (17, "report", ["report"]):
            with self.assertRaises(ValueError):
                plan_lint.lint_mode(env)


class EvTest(_Fixture):
    """FX-13.4: rule EV, priced by the runner's own two functions, extracted into ev_gate."""

    CLEAN_SPEC = "## U.1 first\nDone check: `python3 -B scripts/test_a.py`\nThe suite is `scripts/test_a.py`.\n"
    # 8 builds at 0.02: a 0.16 round, and a landing value of 1.0 (tripled to 3.0 for the unit's one open sub unit)
    ENV = {"BROTHER_WORKER_MIX": "deepseek:8", "BROTHER_ARM_COST": "deepseek:0.02", "BROTHER_VALUE_PER_LANDING": "1.0"}

    def clean(self):
        self.write("scripts/test_a.py", OK_TEST)
        self.write("spec.md", self.CLEAN_SPEC)

    def ledger(self, failed_rounds, sub="U.1"):
        later = time.time() + 1000.0   # newer than the spec, so history counts every row
        rows = [json.dumps({"sub": sub, "run": "R", "round": n, "build": "b0", "grade": "FAIL", "run_at": later})
                for n in range(failed_rounds)]
        return self.write("ledger.jsonl", "".join(r + "\n" for r in rows))

    def ev(self, report):
        return [f for f in report["findings"] if f["rule"] == "EV"]

    def test_landing_value_matches_the_runner_rule(self):
        def runner(open_count, env):   # unit_runner.py's inline expression before FX-13.4, verbatim but for os.environ
            return float(env.get("BROTHER_VALUE_PER_LANDING", ev_gate.DEFAULT_VALUE)) * (3.0 if open_count <= 1 else 1.0)
        for env in ({}, {"BROTHER_VALUE_PER_LANDING": "1.0"}, {"BROTHER_VALUE_PER_LANDING": "2.5"}):
            for open_count in (0, 1, 2, 7):
                self.assertEqual(ev_gate.landing_value(open_count, env), runner(open_count, env), (open_count, env))
        self.assertEqual(ev_gate.landing_value(1, {"BROTHER_VALUE_PER_LANDING": "2"}), 6.0)
        self.assertEqual(ev_gate.landing_value(2, {"BROTHER_VALUE_PER_LANDING": "2"}), 2.0)
        self.assertEqual(ev_gate.landing_value(0, {}), 3.0 * ev_gate.DEFAULT_VALUE)
        with self.assertRaises(ValueError):
            ev_gate.landing_value(1, {"BROTHER_VALUE_PER_LANDING": "x"})

    def test_round_cost_matches_the_runner_rule(self):
        self.assertEqual(ev_gate.DEFAULT_ARM_COST, "deepseek:0.02,muse:0.02,sonnet:0.10,astra:0.10")
        self.assertAlmostEqual(ev_gate.round_cost_from_env({}), 5 * 0.02 + 2 * 0.02 + 0.10)
        self.assertAlmostEqual(ev_gate.round_cost_from_env(self.ENV), 0.16)
        self.assertAlmostEqual(ev_gate.round_cost_from_env({"BROTHER_WORKER_MIX": "jev:2"}), 0.04)   # unpriced arm: 0.02
        self.assertIsNone(ev_gate.round_cost_from_env({"BROTHER_ARM_COST": "deepseek=0.02"}))
        self.assertIsNone(ev_gate.round_cost_from_env({"BROTHER_WORKER_MIX": "deepseek:1", "BROTHER_ARM_COST": "deepseek:0"}))
        self.assertIsNone(ev_gate.round_cost_from_env({"BROTHER_WORKER_MIX": ""}))

    def test_the_runner_calls_the_two_functions(self):
        with open(os.path.join(HERE, "unit_runner.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(src.count("_value = EV.landing_value(len(_open_subs))"), 1)
        self.assertEqual(src.count("_round_cost = EV.round_cost_from_env()"), 1)
        self.assertNotIn('os.environ.get("BROTHER_ARM_COST"', src)
        self.assertNotIn('os.environ.get("BROTHER_VALUE_PER_LANDING"', src)

    def test_ev_with_no_attemptable_round_is_reported(self):
        self.clean()
        report = self.lint([OPEN_UNIT], ledger=self.ledger(20), env=self.ENV)
        ev = self.ev(report)
        self.assertEqual([(f["sub"], f["severity"]) for f in ev], [("U.1", "ADVISORY")])
        reason = ev_gate.should_continue(20, 0, round_cost=ev_gate.round_cost_from_env(self.ENV), value=3.0)[1]
        self.assertIn("not worth another round", reason)
        self.assertIn('"%s"' % reason, ev[0]["text"])
        self.assertIn("FX-50", ev[0]["text"])
        self.assertIn("FX-50", ev[0]["fix"])
        self.assertNotIn("EV", report["not_run"])
        # the repaired twin: a fresh sub unit has its rounds, so no EV finding and nothing else either
        self.assertEqual(self.rules(self.lint([OPEN_UNIT], ledger=self.ledger(0), env=self.ENV)), [])

    def test_an_absent_ledger_is_the_prior_and_no_ledger_is_not_run(self):
        self.clean()
        report = self.lint([OPEN_UNIT], ledger=os.path.join(self.root, "absent.jsonl"), env=self.ENV)
        self.assertEqual(self.rules(report), [])
        self.assertIn("EV", self.lint([OPEN_UNIT], env=self.ENV)["not_run"])

    def test_unreadable_ledger_is_no_data_not_the_prior(self):
        self.clean()
        os.makedirs(os.path.join(self.root, "ledger_dir"))
        bad = self.write_bytes("bad.jsonl", b"\xff\xfe not utf-8\n")
        for path in (os.path.join(self.root, "ledger_dir"), bad, 17, ""):
            report = self.lint([OPEN_UNIT], ledger=path, env=self.ENV)
            self.assertEqual([f["severity"] for f in self.ev(report)], ["NO-DATA"], repr(path))

    def test_unreadable_landing_value_is_no_data(self):
        self.clean()
        for value in ("x", ""):
            env = dict(self.ENV, BROTHER_VALUE_PER_LANDING=value)
            report = self.lint([OPEN_UNIT], ledger=self.ledger(0), env=env)
            self.assertEqual([f["severity"] for f in self.ev(report)], ["NO-DATA"], value)

    def test_admission_reports_ev_and_never_holds_on_it(self):
        self.clean()
        lines, why = plan_lint.admission({"units": [OPEN_UNIT]}, OPEN_UNIT, "U.1", self.root, self.ENV,
                                         ledger=self.ledger(20))
        self.assertEqual(why, "")
        self.assertTrue(any(line.startswith("LINT ADVISORY U U.1 EV:") and "FX-50" in line for line in lines), lines)
        lines, why = plan_lint.admission({"units": [OPEN_UNIT]}, OPEN_UNIT, "U.1", self.root, self.ENV,
                                         ledger=self.ledger(0))
        self.assertEqual((lines, why), ([], ""))
        os.makedirs(os.path.join(self.root, "ledger_dir"))
        lines, why = plan_lint.admission({"units": [OPEN_UNIT]}, OPEN_UNIT, "U.1", self.root, self.ENV,
                                         ledger=os.path.join(self.root, "ledger_dir"))
        self.assertTrue(why.startswith("LINT EV: the ledger"), why)

    def test_hostile_inputs_are_refused_not_crashed(self):
        for count in (True, False, None, "1", 1.5, float("nan"), [1], {}):
            with self.assertRaises(ValueError, msg=repr(count)):
                ev_gate.landing_value(count, {})
        for env in (17, "env", ["x"], {"BROTHER_VALUE_PER_LANDING": None}, {"BROTHER_VALUE_PER_LANDING": [1]}):
            with self.assertRaises(ValueError, msg=repr(env)):
                ev_gate.landing_value(1, env)
        for env in (17, "env", ["x"], {"BROTHER_ARM_COST": None}, {"BROTHER_WORKER_MIX": 5}, {"BROTHER_ARM_COST": "a:b:c"}):
            self.assertIsNone(ev_gate.round_cost_from_env(env), repr(env))
        nan_value = ev_gate.landing_value(1, {"BROTHER_VALUE_PER_LANDING": "nan"})
        self.assertFalse(ev_gate.should_continue(0, 0, value=nan_value, env={})[0])
        self.clean()
        for env in (17, "env", {"BROTHER_VALUE_PER_LANDING": None}):
            report = self.lint([OPEN_UNIT], ledger=self.ledger(0), env=env)
            self.assertEqual([f["severity"] for f in self.ev(report)], ["NO-DATA"], repr(env))
        self.assertEqual(plan_lint._ev_findings("U", None, 1, OPEN_UNIT, self.root, self.ledger(0), {})[0]["severity"],
                         "NO-DATA")


class IntakeTest(_Fixture):
    """FX-13.5: the intake prints the lint, and asks only in block mode. loop_intake.prepare is driven with injected
    probes; the real lint probe runs the CLI in a child process on a fixture plan, with HOME pointed at the fixture."""

    FOUND = ("FINDINGS", "LINT SUMMARY blocking 2 advisory 1 no-data 0 | units 3 sections 4 | scope . | not run: none")
    REG = {"cheap": {"transport": "bridge", "privacy": "public", "kinds": {"build", "grade"}, "quality": {"build": 5, "grade": 5}},
           "strong": {"transport": "claude", "privacy": "private", "kinds": {"build", "grade", "plan", "prose"},
                      "quality": {"build": 9, "grade": 9}}}
    ROLE = {"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False, "default": "cheap"}
    ROLES = {"worker": dict(ROLE, setting="BROTHER_PIN_MODEL"),
             "checker": dict(ROLE, kind="grade", content="private", default="strong"),
             "finisher": dict(ROLE, when="after_run", content="private", must_be_chosen=True, default=None)}
    ABSENT = object()
    REPORT_LINE = "LINT     %s: %s (report mode: information, the verdict is unchanged)"
    RISK = "RISK: units with blocking findings will be skipped by the pool; the report names each"
    # every variable the intake or the lint child reads that could move a verdict here: unset for the test, then restored
    KEYS = ("HOME", "BROTHER_SCOPE", "INTAKE_PROBES", "BROTHER_RUNFLOW_LINT", "BROTHER_TRANSPORTS", "BROTHER_CLAUDE_BIN",
            "BROTHER_CODEX_BIN", "BROTHER_WORKER_MIX")

    def setUp(self):
        super(IntakeTest, self).setUp()
        import loop_intake
        self.intake = loop_intake
        self._here = loop_intake.HERE
        self._saved = dict((k, os.environ.get(k)) for k in self.KEYS)
        for k in self.KEYS:
            os.environ.pop(k, None)
        os.environ["HOME"] = self.root

    def tearDown(self):
        self.intake.HERE = self._here
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super(IntakeTest, self).tearDown()

    def probes(self, lint, **over):
        import datetime
        noon = datetime.datetime(2026, 1, 1, 12, 0)
        ok = lambda: ("OK", "fixture")
        out = dict((k, ok) for k in ("canary", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool"))
        out.update(headroom=lambda: 50.0, hold=lambda: False, now=lambda: noon,
                   reach=lambda plan: [{"status": "OK", "role": r, "model": m, "program": "/p", "version": "1.0",
                                        "cause": "fixture", "cached": False, "binding": None} for r, m in plan])
        if lint is not self.ABSENT:
            out["lint"] = lint
        out.update(over)
        return out

    def prepare(self, answer, env, over=None, **kw):
        lint = answer if answer is self.ABSENT or not isinstance(answer, tuple) else (lambda: answer)
        probes = self.probes(lint, **(over or {}))
        return self.intake.prepare({"finisher": "strong"}, "18:00", 10.0, None, self.ROLES, self.REG, probes, env=env, **kw)[0]

    def lint_lines(self, rec):
        return [line for line in rec["lines"] if line.startswith("LINT") or " lint " in line]

    def test_report_mode_findings_leave_the_verdict_ready(self):
        for env in ({}, {"BROTHER_RUNFLOW_LINT": "report"}, {"BROTHER_RUNFLOW_LINT": ""}):
            for answer in (self.FOUND, ("NO-DATA", "LINT NO-DATA: the plan x cannot be read (y)"), ("OK", "clean")):
                rec = self.prepare(answer, env)
                self.assertEqual(rec["verdict"], "READY", (env, answer, rec["lines"]))
                self.assertEqual(self.lint_lines(rec), [self.REPORT_LINE % answer], (env, answer))
                self.assertEqual(rec["waived"], [])
        # the verdict is exactly what the other conditions make it, whatever the lint says
        for over, verdict in (({"tree": lambda: ("REFUSED", "dirty")}, "NOT READY"),
                              ({"canary": lambda: ("NO-DATA", "no canary")}, "NEEDS YOUR DECISION"),
                              ({"hold": lambda: True}, "HELD")):
            for answer in (self.FOUND, ("NO-DATA", "x"), ("OK", "clean"), self.ABSENT):
                self.assertEqual(self.prepare(answer, {}, over)["verdict"], verdict, (over, answer))

    def test_report_mode_without_a_lint_probe_says_so(self):
        """An old caller's probe dict carries no lint: one NO-DATA line, and the verdict is unchanged."""
        rec = self.prepare(self.ABSENT, {})
        self.assertEqual(rec["verdict"], "READY", rec["lines"])
        self.assertEqual(self.lint_lines(rec), ["LINT     NO-DATA: no lint probe given"])
        rec = self.prepare(self.ABSENT, {"BROTHER_RUNFLOW_LINT": "block"})
        self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", rec["lines"])

    def test_block_mode_findings_ask_and_accept_lint_waives(self):
        block = {"BROTHER_RUNFLOW_LINT": "block"}
        for answer in (self.FOUND, ("NO-DATA", "LINT NO-DATA: the plan x cannot be read (y)")):
            rec = self.prepare(answer, block)
            self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", rec["lines"])
            ask = [line for line in rec["lines"] if line.startswith("ASK")]
            self.assertEqual(len(ask), 1, rec["lines"])
            self.assertTrue(ask[0].startswith("ASK      lint %s: %s  " % answer), ask)
            self.assertIn(self.RISK, ask[0])
            self.assertIn("--accept lint ", ask[0])
            rec = self.prepare(answer, block, accept=("lint",), accepted_by="owner: go")
            self.assertEqual(rec["verdict"], "READY", rec["lines"])
            self.assertEqual(rec["waived"], ["lint"])
            self.assertIn("WAIVED   lint %s: %s  (accepted by: owner: go)" % answer, rec["lines"])
            self.assertEqual(self.prepare(answer, block, accept=("canary",), accepted_by="owner")["verdict"], "NEEDS YOUR DECISION")
            self.assertEqual(self.prepare(answer, block, accept=("lint",), accepted_by=" ")["verdict"], "NOT READY")
            self.assertEqual(self.prepare(answer, block, {"tree": lambda: ("REFUSED", "dirty")}, accept=("lint",),
                                          accepted_by="owner")["verdict"], "NOT READY")
        rec = self.prepare(("OK", "LINT SUMMARY blocking 0 advisory 3 no-data 0 | x"), block)
        self.assertEqual(rec["verdict"], "READY", rec["lines"])
        self.assertIn("OK       lint OK: LINT SUMMARY blocking 0 advisory 3 no-data 0 | x", rec["lines"])

    def test_unknown_mode_is_block_and_the_line_names_the_variable(self):
        blok = {"BROTHER_RUNFLOW_LINT": "blok"}
        rec = self.prepare(self.FOUND, blok)
        self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", rec["lines"])
        self.assertTrue(any(line.startswith("ASK") and "BROTHER_RUNFLOW_LINT=blok" in line for line in rec["lines"]), rec["lines"])
        rec = self.prepare(("OK", "clean"), blok)
        self.assertEqual(rec["verdict"], "READY", rec["lines"])
        self.assertTrue(any(line.startswith("OK       lint OK: clean") and "BROTHER_RUNFLOW_LINT=blok" in line
                            for line in rec["lines"]), rec["lines"])
        os.environ["BROTHER_RUNFLOW_LINT"] = "block"   # no environment injected: prepare reads the process's own
        self.assertEqual(self.prepare(self.FOUND, None)["verdict"], "NEEDS YOUR DECISION")

    def test_a_lint_module_that_cannot_be_imported_is_block_never_off(self):
        real = sys.modules.get("plan_lint")
        sys.modules["plan_lint"] = None   # `import plan_lint` now raises ImportError, as on a copy without the lint
        try:
            rec = self.prepare(self.FOUND, {})
        finally:
            sys.modules["plan_lint"] = real
        self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", rec["lines"])
        self.assertTrue(any(line.startswith("ASK") and "plan_lint.py cannot be imported" in line for line in rec["lines"]), rec["lines"])

    def test_hostile_lint_answers_and_modes_are_refused_never_crashed(self):
        for answer in (None, 17, "OK", ("OK",), ("OK", "a", "b"), ["OK", "clean"], ("MAYBE", "x"), ("ok", "x"),
                       ("OK", None), ("OK", b"x"), ("OK", ["x"]), (["OK"], "x"), ({"OK": 1}, "x"), (float("nan"), "x"),
                       (True, "x"), (None, None)):
            probe = lambda answer=answer: answer
            rec = self.prepare(probe, {})
            self.assertEqual(rec["verdict"], "READY", (answer, rec["lines"]))
            self.assertEqual(len(self.lint_lines(rec)), 1, answer)
            self.assertTrue(self.lint_lines(rec)[0].startswith("LINT     NO-DATA: the lint probe answered a "), rec["lines"])
            self.assertEqual(self.prepare(probe, {"BROTHER_RUNFLOW_LINT": "block"})["verdict"], "NEEDS YOUR DECISION", answer)
        for probe in ("OK", 17, ("OK", "clean"), [lambda: ("OK", "clean")]):
            probes = self.probes(probe)
            rec = self.intake.prepare({"finisher": "strong"}, "18:00", 10.0, None, self.ROLES, self.REG, probes,
                                      env={"BROTHER_RUNFLOW_LINT": "block"})[0]
            self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", (probe, rec["lines"]))
            self.assertTrue(any("not callable" in line for line in rec["lines"]), rec["lines"])
        for value in (17, None, True, ["block"], {"block": 1}, b"report", float("nan")):
            rec = self.prepare(self.FOUND, {"BROTHER_RUNFLOW_LINT": value})
            self.assertEqual(rec["verdict"], "NEEDS YOUR DECISION", (value, rec["lines"]))
            self.assertTrue(any(line.startswith("ASK") and "NO-DATA: lint_mode" in line for line in rec["lines"]), rec["lines"])

    def test_lint_answer_reads_the_exit_and_the_summary_line(self):
        answer = self.intake.lint_answer
        clean = "LINT SUMMARY blocking 0 advisory 2 no-data 0 | units 1 sections 0 | scope . | not run: none"
        found = "LINT SUMMARY blocking 1 advisory 0 no-data 0 | units 1 sections 0 | scope . | not run: none"
        self.assertEqual(answer(0, "LINT ADVISORY U - EV: x | FIX: y\n" + clean + "\n", ""), ("OK", clean))
        self.assertEqual(answer(0, "LINT BLOCKING U - UNIT-CHECK: x | FIX: y\n" + found + "\n", ""), ("FINDINGS", found))
        self.assertEqual(answer(0, "LINT SUMMARY blocking 12 advisory 0 no-data 0 | x", "")[0], "FINDINGS")
        no_data = "LINT SUMMARY blocking 0 advisory 0 no-data 1 | units 1 sections 1 | scope . | not run: none"
        self.assertEqual(answer(3, no_data, ""), ("NO-DATA", no_data))
        self.assertEqual(answer(0, no_data, ""), ("NO-DATA", no_data))   # exit 0 promises no NO-DATA finding
        unreadable = "LINT NO-DATA: the plan p cannot be read (JSONDecodeError: x)"
        self.assertEqual(answer(3, unreadable + "\n", ""), ("NO-DATA", unreadable))
        self.assertEqual(answer(3, clean, ""), ("NO-DATA", clean))
        self.assertEqual(answer(0, clean, "Traceback (most recent call last):\n  File x\nValueError: late"), ("NO-DATA", "ValueError: late"))
        self.assertEqual(answer(1, "", "Traceback (most recent call last):\nKeyError: 'x'\n"), ("NO-DATA", "KeyError: 'x'"))
        self.assertEqual(answer(2, "", "usage: plan_lint.py\nplan_lint.py: error: the following arguments are required: --report"),
                         ("NO-DATA", "plan_lint.py: error: the following arguments are required: --report"))
        self.assertEqual(answer(0, "", ""), ("NO-DATA", "plan_lint.py printed nothing (exit 0)"))
        self.assertEqual(answer(0, clean + "\nsomething after the summary", "")[0], "NO-DATA")
        self.assertEqual(answer(0, " " + clean, "")[0], "OK")
        refused = ("NO-DATA", "plan_lint.py answered in a shape this intake cannot read")
        for code, out, err in ((True, clean, ""), (False, clean, ""), (None, clean, ""), ("0", clean, ""), (0.0, clean, ""),
                               (float("nan"), clean, ""), ([0], clean, ""), (0, None, ""), (0, clean.encode(), ""),
                               (0, [clean], ""), (0, 17, ""), (0, clean, None), (0, clean, b"")):
            self.assertEqual(answer(code, out, err), refused, (code, out, err))

    def write_plan(self, units):
        return self.write(self.intake.LINT_PLAN, json.dumps({"units": units}))

    def test_the_real_probe_runs_the_cli_on_the_launch_plan(self):
        """The CLI beside loop_intake.py, in a child process, on the plan relative to the launch tree (the cwd)."""
        lint = self.intake.real_probes()["lint"]
        prose = {"id": "U", "state": "OPEN", "done_check": "run the tests by hand", "sub_units": [], "evidence": ""}
        self.write_plan([prose])
        state, summary = lint()
        self.assertEqual(state, "FINDINGS", summary)
        self.assertTrue(summary.startswith("LINT SUMMARY blocking 1 advisory 0 no-data 0 | units 1 sections 0 | scope . |"), summary)
        self.write_plan([])
        state, summary = lint()
        self.assertEqual(state, "OK", summary)
        self.assertTrue(summary.startswith("LINT SUMMARY blocking 0 "), summary)
        self.write(self.intake.LINT_PLAN, "{not json")
        state, summary = lint()
        self.assertEqual(state, "NO-DATA", summary)
        self.assertTrue(summary.startswith("LINT NO-DATA: the plan docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json cannot be read"), summary)
        os.remove(os.path.join(self.root, self.intake.LINT_PLAN))
        self.assertEqual(lint()[0], "NO-DATA")
        # the scope is the pool's own, and it reaches the child through the environment
        self.write_plan([prose])
        os.environ["BROTHER_SCOPE"] = "^Z"
        state, summary = self.intake.real_probes()["lint"]()
        self.assertEqual(state, "OK", summary)
        self.assertIn("| units 0 sections 0 | scope ^Z |", summary)

    def fake_lint(self, body):
        fake = os.path.join(self.root, "bin")
        if not os.path.isdir(fake):
            os.makedirs(fake)
        with open(os.path.join(fake, "plan_lint.py"), "w", encoding="utf-8") as fh:
            fh.write(body)
        self.intake.HERE = fake
        return self.intake.real_probes()["lint"]

    def test_the_real_probe_reads_the_child_and_a_crash_is_no_data(self):
        lint = self.fake_lint("import sys\n"
                              "print('LINT SUMMARY blocking 0 advisory 0 no-data 0 | argv %s' % ' '.join(sys.argv[1:]))\n")
        self.assertEqual(lint(), ("OK", "LINT SUMMARY blocking 0 advisory 0 no-data 0 | argv --report "
                                        "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"))
        os.environ["BROTHER_SCOPE"] = "^U"   # through the environment, never a computed argument
        self.assertEqual(self.intake.real_probes()["lint"]()[1], "LINT SUMMARY blocking 0 advisory 0 no-data 0 | argv --report "
                                                                  "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")
        lint = self.fake_lint("print('LINT SUMMARY blocking 0 advisory 0 no-data 0 | x')\nraise KeyError('late')\n")
        self.assertEqual(lint(), ("NO-DATA", "KeyError: 'late'"))
        lint = self.fake_lint("import sys\nprint('LINT SUMMARY blocking 0 advisory 0 no-data 0 | x')\nsys.exit(3)\n")
        self.assertEqual(lint(), ("NO-DATA", "LINT SUMMARY blocking 0 advisory 0 no-data 0 | x"))
        self.intake.HERE = os.path.join(self.root, "empty")
        self.assertEqual(self.intake.real_probes()["lint"](), ("NO-DATA", "plan_lint.py is not beside this copy"))

    def test_the_stub_lint_is_no_data(self):
        self.assertEqual(self.intake.stub_probes()["lint"](), ("NO-DATA", "lint not probed: INTAKE_PROBES=stub"))
        os.environ["INTAKE_PROBES"] = "stub"
        self.assertEqual(self.intake.real_probes()["lint"](), ("NO-DATA", "lint not probed: INTAKE_PROBES=stub"))


POOL = os.path.join(HERE, "runner_pool.py")


class PoolTest(_Fixture):
    """FX-13.6: the pool lints the one sub unit it is about to start. The pool runs IN PROCESS, its main() loaded fresh
    from its file with HOME and the working directory at the fixture (the route test_runner_pool_guard.pool_pass
    takes), so the lint it calls is this module's plan_lint and a case can make that lint crash. --dry: no runner."""

    PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
    START_GREP = re.compile(r"START|^started")      # loop_pass.sh:270 keeps the lines `grep -E 'START|^started'` matches
    REAL_START = re.compile(r"^\S+\s+\S+\s+START$")   # the pool's own start line, "%-5s %-8s START"
    BLOCK = {"BROTHER_RUNFLOW_LINT": "block"}

    def setUp(self):
        super(PoolTest, self).setUp()
        self._environ = dict(os.environ)
        self._argv = list(sys.argv)
        self._path = list(sys.path)
        self._lint = (plan_lint.admission, plan_lint.lint_unit, plan_lint.lint_mode)
        self.home = os.path.join(self.root, "home")
        self.write("home/.claude/bin/burn_guard.py", "print(8)\n")   # the money guard, stated: an unreadable one admits nothing
        self.council = {}

    def tearDown(self):
        plan_lint.admission, plan_lint.lint_unit, plan_lint.lint_mode = self._lint
        sys.argv = self._argv
        sys.path[:] = self._path
        for k in [k for k in os.environ if k not in self._environ]:
            os.environ.pop(k, None)
        os.environ.update(self._environ)
        super(PoolTest, self).tearDown()

    def unit(self, uid, check=None, sub=None):
        """An open unit with one sub unit (`<uid>.1` unless named): its section names a file of its own and a test that
        is on disk, so it has no finding. `check` replaces the unit's done check, the one defect of a flagged unit."""
        sub = sub or uid + ".1"
        test = "scripts/test_%s.py" % uid.lower()
        self.write(test, OK_TEST)
        spec = "docs/plan/specs/%s.md" % uid
        self.write(spec, "# %s\n\n### %s the one sub unit\nIt changes `scripts/%s_1.py`.\nDone check: `python3 -B %s`\n"
                   % (uid, sub, uid.lower(), test))
        return {"id": uid, "state": "OPEN", "done_check": check or "python3 -B %s" % test, "spec": spec,
                "sub_units": [sub], "evidence": ""}

    def verdict(self, uid, state, claims=()):
        """A council entry for `uid`, and one prompt holding its current spec text, so the verdict is not STALE."""
        self.council[uid] = {"state": state, "blockers": [{"claim": c} for c in claims]}
        self.write("home/.claude/evidence/spec-council.json", json.dumps(self.council))
        with open(os.path.join(self.root, "docs/plan/specs/%s.md" % uid), encoding="utf-8") as fh:
            text = fh.read()
        self.write("home/.claude/evidence/spec-council/%s/prompts/seat-1.md" % uid, "Judge this spec.\n\n" + text)

    def pool_pass(self, units, env=None, mod=None):
        """One --dry pass of the pool over `units`: (stdout, the pool module). `mod` reuses a loaded pool, two passes in
        one process; None loads it fresh, a restarted pool. The plan is replaced whole (os.replace, as close_unit's
        write_atomic does). Every BROTHER_ and GIT_ variable is cleared, the finish-first gate is off (it reads git and
        gh), and `env` is applied last."""
        plan = os.path.join(self.root, self.PLAN)
        if not os.path.isdir(os.path.dirname(plan)):
            os.makedirs(os.path.dirname(plan))
        with open(plan + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"units": units}, fh)
        os.replace(plan + ".tmp", plan)
        scores = dict((s, {"score": 9, "missing": []}) for u in units for s in u["sub_units"])
        self.write("home/.claude/evidence/spec-scores.json", json.dumps(scores))
        for k in [k for k in os.environ if k.startswith("BROTHER_") or k.startswith("GIT_")]:
            os.environ.pop(k, None)
        os.environ.update({"HOME": self.home, "PYTHONDONTWRITEBYTECODE": "1", "BROTHER_WIP_GATE": "off"})
        os.environ.update(env or {})
        sys.argv = [POOL, "--dry"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)   # the pool's own json.load(open(...)) reads, not this unit's
            if mod is None:
                spec = importlib.util.spec_from_file_location("runner_pool_fx136", POOL)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            mod.main()
        return (buf.getvalue(), mod)

    def started(self, out):
        return set(re.findall(r"(?m)^\S+\s+(\S+)\s+START$", out))

    def lint_lines(self, out):
        return [line for line in out.splitlines() if re.match(r"\S+\s+\S+\s+LINT ", line)]

    def two_units(self):
        """A clean unit and its twin flagged by one defect: a prose unit done check (UNIT-CHECK BLOCKING)."""
        units = [self.unit("A"), self.unit("B", check="run the tests by hand")]
        self.verdict("A", "DESIGN-CLEAR")
        self.verdict("B", "DESIGN-CLEAR")
        return units

    def test_report_mode_admits_what_today_admits(self):
        units = self.two_units()
        plan_lint.admission = lambda *args, **kw: ([], "")   # today: the pool had no lint
        today, _ = self.pool_pass(units)
        plan_lint.admission = self._lint[0]
        self.assertEqual(self.started(today), {"A.1", "B.1"}, today)
        for env in ({}, {"BROTHER_RUNFLOW_LINT": "report"}, {"BROTHER_RUNFLOW_LINT": ""}):
            out, _ = self.pool_pass(units, env)
            self.assertEqual(self.started(out), self.started(today), (env, out))
            self.assertEqual(re.findall(r"(?m)^LINT    mode .*$", out), ["LINT    mode report"], (env, out))
            lines = self.lint_lines(out)
            self.assertEqual(len(lines), 1, (env, out))
            self.assertTrue(lines[0].startswith("B     B.1      LINT BLOCKING B - UNIT-CHECK: the unit done_check is not a "
                                                "shape the closer will run: run the tests by hand | FIX: "), lines)
            self.assertNotIn("skip: LINT", out)

    def test_block_mode_skips_a_flagged_unit_with_the_lint_word(self):
        units = self.two_units()
        out, _ = self.pool_pass(units, self.BLOCK)
        self.assertEqual(re.findall(r"(?m)^LINT    mode .*$", out), ["LINT    mode block"], out)
        self.assertEqual(self.started(out), {"A.1"}, out)
        self.assertRegex(out, r"(?m)^B\s+B\.1\s+skip: LINT UNIT-CHECK: the unit done_check is not a shape the closer "
                              r"will run: run the tests by hand$")
        self.assertTrue(any(line.startswith("B     B.1      LINT BLOCKING B - UNIT-CHECK:") for line in self.lint_lines(out)), out)
        # an unknown value is block, never off, and the mode line names the variable
        out, _ = self.pool_pass(units, {"BROTHER_RUNFLOW_LINT": "blok"})
        self.assertEqual(re.findall(r"(?m)^LINT    mode .*$", out),
                         ["LINT    mode block  (NO-DATA: BROTHER_RUNFLOW_LINT=blok is not report or block)"], out)
        self.assertEqual(self.started(out), {"A.1"}, out)
        self.assertRegex(out, r"(?m)^B\s+B\.1\s+skip: LINT UNIT-CHECK: ")

    def test_a_unit_added_after_the_first_pass_is_linted_at_admission(self):
        a = self.unit("A")
        self.verdict("A", "DESIGN-CLEAR")
        out, mod = self.pool_pass([a], self.BLOCK)
        self.assertEqual((self.started(out), self.lint_lines(out)), ({"A.1"}, []), out)
        b = self.unit("B", check="run the tests by hand")   # added to the plan by a second actor between two passes
        self.verdict("B", "DESIGN-CLEAR")
        out, _ = self.pool_pass([a, b], self.BLOCK, mod=mod)
        self.assertEqual(self.started(out), {"A.1"}, out)
        self.assertTrue(any(line.startswith("B     B.1      LINT BLOCKING B - UNIT-CHECK:") for line in self.lint_lines(out)), out)
        self.assertRegex(out, r"(?m)^B\s+B\.1\s+skip: LINT UNIT-CHECK: ")
        # and a unit edited after the first pass: A's check turns to prose, the third pass lints the edit
        out, _ = self.pool_pass([dict(a, done_check="run it by hand"), b], self.BLOCK, mod=mod)
        self.assertEqual(self.started(out), set(), out)
        self.assertRegex(out, r"(?m)^A\s+A\.1\s+skip: LINT UNIT-CHECK: .*run it by hand$")

    def test_restarted_pool_prints_the_same_lint_lines(self):
        units = self.two_units() + [self.unit("C")]   # C has no council entry: an ADVISORY, printed, never a hold
        first, _ = self.pool_pass(units)
        second, _ = self.pool_pass(units)   # loaded afresh: the pool restarted, same plan, same environment
        self.assertEqual(self.lint_lines(first), self.lint_lines(second))
        self.assertEqual(len(self.lint_lines(first)), 2, first)
        self.assertTrue(any(line.startswith("C     C.1      LINT ADVISORY C - COUNCIL-ABSENT:") for line in self.lint_lines(first)), first)
        self.assertEqual(self.started(first), self.started(second))

    def test_no_lint_line_matches_the_start_grep(self):
        # C's check names a test file carrying the word, absent and not labelled NEW, so TEST-FOUND quotes it; D's sub
        # unit id carries it, and D has no council entry, so it is admitted in every mode with an ADVISORY line
        c = self.unit("C", check="python3 -B scripts/test_START.py")
        d = self.unit("D", sub="D.START")
        self.verdict("C", "DESIGN-CLEAR")
        for env, starts in (({}, {"C.1", "D.START"}), (self.BLOCK, {"D.START"}), ({"BROTHER_RUNFLOW_LINT": "START"}, {"D.START"})):
            out, _ = self.pool_pass([c, d], env)
            counted = [line for line in out.splitlines() if self.START_GREP.search(line)]
            self.assertEqual([line for line in counted if not self.REAL_START.match(line) and not line.startswith("started ")],
                             [], (env, out))
            self.assertEqual(self.started(out), starts, (env, out))
            self.assertIn("the command names the test file scripts/test_Start.py, which is not on disk", out)
            self.assertTrue(any(line.startswith("D     D.Start  LINT ADVISORY D - COUNCIL-ABSENT:") for line in out.splitlines()), out)
        self.assertRegex(out, r"(?m)^LINT    mode block  \(NO-DATA: BROTHER_RUNFLOW_LINT=Start is not report or block\)$")
        self.assertRegex(out, r"(?m)^C\s+C\.1\s+skip: LINT TEST-FOUND: the command names the test file scripts/test_Start\.py")

    def test_a_lint_crash_never_crashes_the_pool(self):
        units = self.two_units()

        def crash(*args, **kw):
            raise RuntimeError("the lint fell over")
        plan_lint.lint_unit = crash
        out, _ = self.pool_pass(units)   # report: the crash is one NO-DATA line, admission is today's
        self.assertEqual(self.started(out), {"A.1", "B.1"}, out)
        self.assertEqual(self.lint_lines(out), ["A     A.1      LINT NO-DATA: RuntimeError",
                                                "B     B.1      LINT NO-DATA: RuntimeError"], out)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("started 2 |"), out)
        out, _ = self.pool_pass(units, self.BLOCK)   # block: skipped, and the pass still runs to its end
        self.assertEqual(self.started(out), set(), out)
        self.assertRegex(out, r"(?m)^A\s+A\.1\s+skip: LINT NO-DATA: the lint could not run \(RuntimeError\)$")
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("started 0 |"), out)

    def test_a_council_hold_is_printed_and_skips_only_in_block_mode(self):
        units = [self.unit("A"), self.unit("B")]
        self.verdict("A", "DESIGN-CLEAR")
        self.verdict("B", "FIX-FIRST", ["the section names no test"])
        out, _ = self.pool_pass(units)
        self.assertEqual(self.started(out), {"A.1", "B.1"}, out)
        self.assertEqual(self.lint_lines(out), ["B     B.1      LINT BLOCKING B - COUNCIL: council FIX-FIRST: the section names "
                                                "no test | FIX: answer every claim in the spec, then re-run the spec council"], out)
        out, _ = self.pool_pass(units, self.BLOCK)
        self.assertEqual(self.started(out), {"A.1"}, out)
        self.assertRegex(out, r"(?m)^B\s+B\.1\s+skip: LINT COUNCIL: council FIX-FIRST: the section names no test$")
        # a council file that cannot be read is NO-DATA, never an empty verdict list: block mode starts nothing
        os.remove(os.path.join(self.home, ".claude", "evidence", "spec-council.json"))
        out, _ = self.pool_pass(units, self.BLOCK)
        self.assertEqual(self.started(out), set(), out)
        self.assertRegex(out, r"(?m)^A\s+A\.1\s+skip: LINT COUNCIL: the council file \S+ cannot be read \(FileNotFoundError\)$")

    def test_an_unreadable_mode_is_block_never_off(self):
        units = self.two_units()

        def unreadable(env=None):
            raise ValueError("lint_mode: BROTHER_RUNFLOW_LINT must be text, got int")
        plan_lint.lint_mode = unreadable
        out, _ = self.pool_pass(units)
        self.assertEqual(re.findall(r"(?m)^LINT    mode .*$", out),
                         ["LINT    mode block  (NO-DATA: lint_mode: BROTHER_RUNFLOW_LINT must be text, got int)"], out)
        self.assertEqual(self.started(out), {"A.1"}, out)
        self.assertRegex(out, r"(?m)^B\s+B\.1\s+skip: LINT UNIT-CHECK: ")

    def test_hostile_inputs_are_refused_never_admitted(self):
        a = self.unit("A")
        self.verdict("A", "DESIGN-CLEAR")
        plan = {"units": [a]}
        council = os.path.join(self.home, ".claude", "evidence", "spec-council.json")
        ledger = os.path.join(self.root, "absent.jsonl")

        def lint(plan=plan, unit=a, sub="A.1", root=self.root, env=None, council=council):
            return plan_lint.admission(plan, unit, sub, root, {} if env is None else env, ledger=ledger, council=council,
                                       council_dir=os.path.join(self.home, ".claude", "evidence", "spec-council"))
        self.assertEqual(lint(), ([], ""))   # the clean twin
        refused = "LINT NO-DATA: the sub unit id is not text"
        for sub in (None, 17, True, 1.5, float("nan"), ["A.1"], {"id": "A.1"}, ("A.1",), b"A.1", ""):
            self.assertEqual(lint(sub=sub), ([refused], refused), repr(sub))
        for plan_value, unit_value in ((None, a), ([a], a), ("plan", a), (plan, None), (plan, ["A"]), (plan, "A")):
            lines, why = lint(plan=plan_value, unit=unit_value)
            self.assertEqual(why, "LINT NO-DATA: the plan or the unit is not a record", repr((plan_value, unit_value)))
        for root in (None, 17, ["."], b"."):
            lines, why = lint(root=root)
            self.assertTrue(why.startswith("LINT ") and "NO-DATA" in " ".join(lines), (root, lines, why))
        for env in (17, "env", ["x"], {"BROTHER_VALUE_PER_LANDING": None}, {"BROTHER_VALUE_PER_LANDING": [1]}):
            lines, why = lint(env=env)
            self.assertTrue(why.startswith("LINT EV: the landing value cannot be read"), (env, why))
        for value in (17, ["A"], {"A": "DESIGN-CLEAR"}, os.path.join(self.root, "absent.json")):
            lines, why = lint(council=value)
            self.assertTrue(why.startswith("LINT COUNCIL: "), (value, why))


if __name__ == "__main__":
    unittest.main()
