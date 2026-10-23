#!/usr/bin/env python3
"""LC2.a (A3): every top level tool the loop mirror ships, imported alone, loads nothing from the source
checkout and finds every module it needs in the mirror. Each fixture below trips exactly one condition of
scripts/loop_alone.py; the last one runs the real loop, mirrored fresh from scripts/loop.

Run: python3 -B scripts/test_loop_alone.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_alone as LA  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

LOOP_SOURCE = os.path.join(HERE, "loop")


class TheInstalledLoopStandsAlone(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="loop-alone-test-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def fake_loop(self, tools):
        """A source scripts/ holding helper_src.py (a module only the source has) and a mirror of `tools` the
        way the landing mirrors it: <runtime>/loop/<name> plus the mirror's own LOOP-MANIFEST.json."""
        scripts = os.path.join(self.d, "scripts")
        runtime = os.path.join(self.d, "runtime")
        mirror = os.path.join(runtime, LA.LOOP_DIR_NAME)
        os.makedirs(scripts)
        os.makedirs(mirror)
        with open(os.path.join(scripts, "helper_src.py"), "w", encoding="utf-8") as fh:
            fh.write("H = 1\n")
        rows = []
        for name in sorted(tools):
            with open(os.path.join(mirror, name), "w", encoding="utf-8") as fh:
                fh.write(tools[name])
            rows.append({"name": name, "sha256": "0" * 64})
        with open(os.path.join(mirror, LA.LOOP_MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump({"files": rows}, fh)
        return scripts, runtime

    def test_a_clean_tool_loads_and_is_counted(self):
        scripts, runtime = self.fake_loop({"ok.py": "X = 1\n"})
        self.assertEqual(LA.check_loop_alone(runtime, scripts), ([], 1))

    def test_a_tool_that_loads_from_the_source_checkout_is_named(self):
        source = os.path.join(self.d, "scripts")
        scripts, runtime = self.fake_loop(
            {"leak.py": "import sys\nsys.path.insert(0, %r)\nimport helper_src\n" % source})
        problems, loaded = LA.check_loop_alone(runtime, scripts)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("loop/leak.py: loads ", problems[0])
        self.assertIn("helper_src.py", problems[0])
        self.assertEqual(loaded, 0)

    def test_a_tool_that_cannot_find_a_module_in_the_mirror_is_named(self):
        scripts, runtime = self.fake_loop({"needs.py": "import lc2_no_such_module_anywhere\n"})
        self.assertEqual(LA.check_loop_alone(runtime, scripts),
                         (["loop/needs.py: cannot find module lc2_no_such_module_anywhere in the mirror alone"], 0))

    def test_a_tool_that_dies_before_answering_is_named(self):
        scripts, runtime = self.fake_loop({"slow.py": "import time\ntime.sleep(30)\n"})
        self.assertEqual(LA.check_loop_alone(runtime, scripts, 1),
                         (["loop/slow.py: the probe printed no answer"], 0))

    def test_a_tool_whose_import_fails_for_another_reason_is_neither_a_problem_nor_loaded(self):
        scripts, runtime = self.fake_loop({"dies.py": "raise SystemExit(3)\n"})
        self.assertEqual(LA.check_loop_alone(runtime, scripts), ([], 0))

    def test_a_tool_missing_from_the_mirror_is_named(self):
        scripts, runtime = self.fake_loop({"ok.py": "X = 1\n", "gone.py": "Y = 2\n"})
        os.remove(os.path.join(runtime, LA.LOOP_DIR_NAME, "gone.py"))
        self.assertEqual(LA.check_loop_alone(runtime, scripts), (["loop/gone.py: missing from the mirror"], 1))

    def test_no_tool_to_probe_is_a_problem_never_a_pass(self):
        scripts, runtime = self.fake_loop({"notes.md": "n\n", "test_x.py": "T = 1\n"})
        problems, loaded = LA.check_loop_alone(runtime, scripts)
        self.assertEqual(loaded, 0)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("names no tool to probe", problems[0])

    def test_an_unreadable_manifest_is_a_problem_never_a_pass(self):
        scripts, runtime = self.fake_loop({"ok.py": "X = 1\n"})
        with open(os.path.join(runtime, LA.LOOP_DIR_NAME, LA.LOOP_MANIFEST_NAME), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        problems, loaded = LA.check_loop_alone(runtime, scripts)
        self.assertEqual(loaded, 0)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("is missing or unreadable", problems[0])

    def test_the_command_line_passes_on_a_mirror_that_stands_alone(self):
        scripts, runtime = self.fake_loop({"ok.py": "X = 1\n"})
        out = io.StringIO()
        with redirect_stdout(out):
            code = LA.main(["loop_alone.py", runtime])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("PASS loop_alone: 1 tool(s) loaded alone from " + runtime, out.getvalue())

    def test_the_command_line_fails_on_a_mirror_with_a_problem(self):
        scripts, runtime = self.fake_loop({"needs.py": "import lc2_no_such_module_anywhere\n"})
        out = io.StringIO()
        with redirect_stdout(out):
            code = LA.main(["loop_alone.py", runtime])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("FAIL loop/needs.py: cannot find module lc2_no_such_module_anywhere", out.getvalue())
        self.assertNotIn("PASS", out.getvalue())

    def test_the_command_line_fails_when_nothing_loads(self):
        scripts, runtime = self.fake_loop({"dies.py": "raise SystemExit(3)\n"})
        out = io.StringIO()
        with redirect_stdout(out):
            code = LA.main(["loop_alone.py", runtime])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("0 tool(s) loaded alone", out.getvalue())
        self.assertNotIn("PASS", out.getvalue())

    def test_the_real_loop_mirrored_fresh_stands_alone(self):
        runtime = os.path.join(self.d, "real-runtime")
        mirror = os.path.join(runtime, LA.LOOP_DIR_NAME)
        shutil.copytree(LOOP_SOURCE, mirror)
        names = sorted(n for n in os.listdir(mirror) if n.endswith(".py"))
        with open(os.path.join(mirror, LA.LOOP_MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump({"files": [{"name": n, "sha256": "0" * 64} for n in names]}, fh)
        problems, loaded = LA.check_loop_alone(runtime, HERE, 30)
        self.assertEqual(problems, [], problems)
        self.assertGreater(loaded, 0)


class EveryPublicFunctionRefusesHostileInput(unittest.TestCase):
    """The landing fuzz calls every public function of this module with None, 0, True, -1, NaN, "", "x", b"x",
    [], ["x"], {}, {"a": 1}, (), {1, 2} and a bare object in every positional parameter. Each of those calls must
    RETURN or REFUSE with this module's own ValueError or with SystemExit: a raw TypeError out of os.path.join,
    open, len or threading is a crash and drops the whole build."""

    VALUES = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp(prefix="loop-alone-hostile-")
        os.chdir(self._tmp)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_mirrored_tools_refuses_or_answers_on_every_hostile_value(self):
        for value in self.VALUES:
            try:
                names, why = LA.mirrored_tools(value)
            except ValueError:
                continue
            self.assertIsInstance(names, list)
            self.assertIsInstance(why, str)

    def test_check_loop_alone_refuses_or_answers_on_every_hostile_value(self):
        for value in self.VALUES:
            for args in ((value,), (value, value), (value, value, value)):
                try:
                    problems, loaded = LA.check_loop_alone(*args)
                except ValueError:
                    continue
                self.assertIsInstance(problems, list)
                self.assertIsInstance(loaded, int)

    def test_main_refuses_or_answers_on_every_hostile_value(self):
        saved_argv = sys.argv
        saved_runtime = LA.RUNTIME_DIR
        sys.argv = ["loop_alone"]
        LA.RUNTIME_DIR = os.path.join(self._tmp, "runtime")
        try:
            for value in self.VALUES:
                try:
                    with redirect_stdout(io.StringIO()):
                        code = LA.main(value)
                except SystemExit as exc:
                    self.assertEqual(exc.code, 2)
                    continue
                self.assertIn(code, (0, 1))
        finally:
            LA.RUNTIME_DIR = saved_runtime
            sys.argv = saved_argv


if __name__ == "__main__":
    unittest.main()
