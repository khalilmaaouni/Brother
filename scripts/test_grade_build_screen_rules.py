#!/usr/bin/env python3
"""FX-09.4: the grader exports its own screen as text, and every line of that text agrees with what unsafe() refuses.

WHY: a builder never saw the grader's real screen. The only statement of it was a hand typed sentence that had drifted
(it forbade shutil, which the grader allows, and never named the rules that refused most builds: repository modules,
import_module on a computed name, os.fork and friends, vars()). screen_rules renders the screen FROM THE GRADER'S OWN
CONSTANTS AND FUNCTIONS, so these tests feed every printed name back into unsafe() and require the same answer.
Tests the loop's grader, scripts/loop/grade_build.py. Run: python3 -B scripts/test_grade_build_screen_rules.py"""
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import grade_build as G  # noqa: E402


def _build(code, path="scripts/sample.py"):
    return {"unit": "Z", "sub": "Z.1", "edits": [{"path": path, "new_file_content": code}], "tests": [],
            "mutations": [], "done_check": "python3 -B scripts/test_sample.py"}


def _names(text, label):
    """The comma separated names printed after `label:` up to the first semicolon."""
    m = re.search(re.escape(label) + r"[^:\n]*: ([^;\n]+);", text)
    if not m:
        raise AssertionError("no %r line in the screen:\n%s" % (label, text))
    return [n.strip() for n in m.group(1).split(",") if n.strip()]


class TheScreenComesFromTheGrader(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="screen-rules-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts"))
        for rel, text in (("scripts/ok_mod.py", "import json\n"),
                          ("scripts/bad_mod.py", "import subprocess\n"),
                          ("scripts/indirect_mod.py", "import bad_mod\n"),
                          ("scripts/test_ok_mod.py", "import ok_mod\n")):
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        G._REPO_SAFE.clear()
        self.addCleanup(G._REPO_SAFE.clear)

    def screen(self, paths=(), runners=()):
        return G.screen_rules(list(paths), runners=set(runners))

    def test_the_block_starts_with_its_head(self):
        text = self.screen()
        self.assertTrue(text.startswith(G.SCREEN_HEAD + "\n"), text[:200])
        self.assertEqual(text.count(G.SCREEN_HEAD), 1)

    def test_every_denied_module_printed_is_refused_by_unsafe(self):
        denied = _names(self.screen(), "IMPORTS REFUSED")
        self.assertEqual(set(denied), set(G.NET_MODULES) | set(G.DENY_TOP))
        for name in denied:
            self.assertIsNotNone(G.unsafe(_build("import %s\n" % name), runners=set()), name)
        # the drift that started this unit: shutil was forbidden by hand and is allowed by the grader
        self.assertNotIn("shutil", denied)
        self.assertIsNone(G.unsafe(_build("import shutil\n"), runners=set()))

    def test_every_refused_call_printed_is_refused_by_unsafe(self):
        text = self.screen()
        calls = [c[:-2] for c in _names(text, "CALLS REFUSED")]
        self.assertEqual(set(calls), set(G.BAD_CALLS))
        for name in calls:
            self.assertIsNotNone(G.unsafe(_build("%s('x')\n" % name), runners=set()), name)
        dynamic = [c[:-2] for c in _names(text, "ALWAYS REFUSED")]
        self.assertEqual(set(dynamic), set(G.DYNAMIC_CALLS))
        for name in dynamic:
            self.assertIsNotNone(G.unsafe(_build("x = %s()\n" % name), runners=set()), name)
        os_calls = _names(text, "OS CALLS REFUSED")
        self.assertEqual(set(os_calls), {"os." + a for a in G.OS_EXEC_ATTRS})
        for dotted in os_calls:
            self.assertIsNotNone(G.unsafe(_build("import os\n%s()\n" % dotted), runners=set()), dotted)
        # the one exception the line states: a METHOD named run or call on an ordinary object passes
        for m in sorted(G.ORDINARY_METHODS):
            self.assertIn(m, _names(text, "EXCEPT A METHOD NAMED"))
            self.assertIsNone(G.unsafe(_build("obj = object()\nobj.%s()\n" % m), runners=set()), m)

    def test_module_verdicts_agree_with_unsafe(self):
        paths = ["scripts/ok_mod.py", "scripts/bad_mod.py", "scripts/indirect_mod.py", "scripts/test_ok_mod.py"]
        text = self.screen(paths)
        for rel, stem in (("scripts/ok_mod.py", "ok_mod"), ("scripts/bad_mod.py", "bad_mod"),
                          ("scripts/indirect_mod.py", "indirect_mod")):
            line = next((l for l in text.splitlines() if l.strip().startswith(rel + ":")), None)
            self.assertIsNotNone(line, "%s has no module line:\n%s" % (rel, text))
            G._REPO_SAFE.clear()
            refused = G.unsafe(_build("import %s\n" % stem, path="scripts/other.py"), runners=set())
            if refused:
                self.assertIn("NOT importable", line, refused)
            else:
                self.assertIn(": importable", line)
                self.assertNotIn("NOT", line)
        self.assertIn("scripts/ok_mod.py: importable", text)
        self.assertIn("scripts/bad_mod.py: NOT importable", text)
        self.assertIn("scripts/indirect_mod.py: NOT importable", text)
        self.assertNotIn("scripts/test_ok_mod.py:", text)       # a test is never a module line

    def test_the_shown_modules_header_heads_the_module_lines(self):
        # a module line alone ("scripts/ok_mod.py: importable") does not say whose verdict it is; the header does, so
        # it stands exactly once right above the module lines, and is absent when no module line is printed
        lines = self.screen(["scripts/ok_mod.py", "scripts/bad_mod.py"]).splitlines()
        heads = [i for i, l in enumerate(lines) if l.startswith("- SHOWN MODULES, the grader's verdict")]
        self.assertEqual(len(heads), 1, lines)
        self.assertEqual([l.split(":")[0].strip() for l in lines[heads[0] + 1:]],
                         ["scripts/ok_mod.py", "scripts/bad_mod.py"])
        self.assertNotIn("SHOWN MODULES", self.screen(["scripts/test_ok_mod.py", "docs/notes.md"]))

    def test_a_loop_module_line_judges_its_bare_stem_too(self):
        # a safe scripts/loop/x.py whose bare stem `x` resolves to an unsafe scripts/x.py twin: the grader refuses
        # `import x`, so the line must say NOT importable (the AND of the dotted path and the bare stem verdicts)
        os.makedirs(os.path.join(self.root, "scripts", "loop"))
        for rel, text in (("scripts/x.py", "import subprocess\n"), ("scripts/loop/x.py", "import json\n")):
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        text = self.screen(["scripts/loop/x.py"])
        self.assertIn("scripts/loop/x.py: NOT importable", text)
        G._REPO_SAFE.clear()
        self.assertIsNotNone(G.unsafe(_build("import x\n", path="scripts/loop/test_x.py"), runners=set()))

    def test_runner_lines_only_for_shown_runner_paths(self):
        text = self.screen(["scripts/ok_mod.py"], runners={"scripts/ok_mod.py", "scripts/elsewhere.py"})
        runners = _names(text, "COMMAND RUNNERS")
        self.assertEqual(runners, ["scripts/ok_mod.py"])
        self.assertNotIn("scripts/elsewhere.py", text)
        self.assertIn("scripts/ok_mod.py: importable (command runner)", text)
        text = self.screen(["scripts/ok_mod.py"], runners=())
        self.assertEqual(_names(text, "COMMAND RUNNERS"), ["none shown"])
        self.assertNotIn("(command runner)", text)

    def test_screen_rules_without_runners_names_the_plans_command_runners(self):
        # build_brief calls screen_rules(existing) with NO runners argument, so the allowed_runners() default is the
        # live path: a shown runner must be named on the COMMAND RUNNERS line and tagged on its module line
        from unittest import mock
        with mock.patch.object(G, "allowed_runners", return_value={"scripts/ok_mod.py", "scripts/elsewhere.py"}):
            text = G.screen_rules(["scripts/ok_mod.py"])
        self.assertEqual(_names(text, "COMMAND RUNNERS"), ["scripts/ok_mod.py"])
        self.assertIn("scripts/ok_mod.py: importable (command runner)", text)

    def test_the_imports_allowed_line_lists_every_os_exec_attribute(self):
        text = self.screen()
        m = re.search(r"calls none of ([^;]*); a module THIS build", text)
        self.assertIsNotNone(m, text)
        self.assertEqual({n.strip() for n in m.group(1).split(",")}, {"os." + a for a in G.OS_EXEC_ATTRS})

    def test_the_runner_rules_come_from_the_constants(self):
        text = self.screen()
        self.assertEqual(set(_names(text, "NETWORK BINARIES")), set(G.NET_BINARIES))
        self.assertEqual(set(_names(text, "GIT VERBS")), set(G.NET_GIT_VERBS))
        self.assertEqual(set(_names(text, "INTERPRETERS")), set(G.INTERPRETERS))

    def test_the_preflight_rules_print_the_graders_own_values(self):
        text = self.screen()
        self.assertIn(G.OK_CMD.pattern, text)
        self.assertIn("at least %d mutations" % G.MIN_MUTATIONS, text)
        self.assertIn(G.ALWAYS_TEXT.pattern, text)
        self.assertIn(G.BLOCK.pattern, text)

    def test_screen_cap_keeps_the_rules_and_counts_the_rest(self):
        rules = self.screen()
        paths = ["scripts/m%03d.py" % i for i in range(500)]
        text = self.screen(paths)
        self.assertLessEqual(len(text.encode("utf-8")), G.SCREEN_CAP)
        self.assertTrue(text.startswith(rules.rstrip("\n")), "the rule lines must survive the cap, in order")
        listed = [p for p in paths if (p + ":") in text]
        self.assertTrue(listed, "some module lines fit under the cap")
        self.assertEqual(listed, paths[:len(listed)], "module lines are dropped from the END")
        self.assertIn("%d more shown modules are not listed here" % (500 - len(listed)), text)

    def test_the_whole_rule_block_fits_the_cap_alone(self):
        self.assertLess(len(self.screen().encode("utf-8")), G.SCREEN_CAP // 2)

    def test_screen_rules_refuses_non_list_paths(self):
        for bad in ("scripts/a.py", None, {"scripts/a.py"}, [1], [b"scripts/a.py"], (p for p in ["a.py"])):
            with self.assertRaises(ValueError, msg=repr(bad)):
                G.screen_rules(bad, runners=set())
        for bad in ("scripts/a.py", 3, [None]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                G.screen_rules([], runners=bad)

    def test_no_line_reads_as_a_file_block_or_a_wbs_block(self):
        text = self.screen(["scripts/ok_mod.py", "scripts/bad_mod.py"], runners={"scripts/ok_mod.py"})
        for line in text.splitlines():
            self.assertFalse(line.startswith("===== FILE: "), line)
            self.assertFalse(line.startswith("THIS UNIT, FROM THE WBS"), line)


HEADER = "- SHOWN MODULES, the grader's verdict on importing each when your build does not edit it:"
YES = ": importable"
NO = ": NOT importable unless your build edits it"
TAIL = "%d more shown modules are not listed here; each is judged by the rules above"


class ScreenRulesContract(unittest.TestCase):
    """FX-09 round 5: the module section of screen_rules as one table, a row per documented class (the docstring and
    spec 5.3 items 6, 8 and 9). Rounds 1 to 4 pinned survivors one at a time and each verify found new ones; this table
    enumerates the contract instead: counts 0, 1 and many of shown modules, of runners and of cut modules; which paths
    are modules; the names a line is judged by (dotted path, a package's __init__ folded to the package, the bare stem
    for scripts/ and scripts/loop/ only); byte cap in BYTES, never characters."""

    FILES = (("scripts/ok_mod.py", "import json\n"), ("scripts/bad_mod.py", "import subprocess\n"),
             ("scripts/mytest_mod.py", "import json\n"),            # "test" inside the name, not a test_ prefix
             ("scripts/socket.py", "import json\n"),                # safe by its dotted path, its stem is denied
             ("scripts/pkg/__init__.py", "import json\n"),          # `import pkg` resolves to nothing the grader reads
             ("plugin/core/__init__.py", "import json\n"),
             ("plugin/core/good.py", "import json\n"), ("plugin/core/bad.py", "import subprocess\n"),
             ("scripts/x.py", "import subprocess\n"), ("scripts/loop/x.py", "import json\n"),
             ("scripts/loop/lonely.py", "import json\n"),
             ("scripts/subprocess.py", "import json\n"),         # safe content; its bare stem is subprocess
             ("scripts/test_ok_mod.py", "import ok_mod\n"), ("scripts/loop/test_x.py", "import x\n"))

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="screen-contract-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel, text in self.FILES:
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        G._REPO_SAFE.clear()
        self.addCleanup(G._REPO_SAFE.clear)

    def section(self, text):
        """The lines after the last rule line: the header, the module lines and the tail, nothing else."""
        lines = text.splitlines()
        last = [i for i, l in enumerate(lines) if l.startswith("- PREFLIGHT AND APPLY")]
        self.assertEqual(len(last), 1, text)
        return lines[last[0] + 1:]

    def test_the_module_section_contract(self):
        long_path = "scripts/" + "z" * 9000 + ".py"           # alone over SCREEN_CAP: cut, and counted
        room = G.SCREEN_CAP - len(G.screen_rules(["scripts/ok_mod.py"], runners=set()).encode("utf-8"))
        fixed = len(("  scripts/.py" + NO + "\n").encode("utf-8"))
        k = (room - fixed) * 3 // 4                           # k + fixed characters fit, 2k + fixed bytes do not
        wide_path = "scripts/" + "\u00e9" * k + ".py"
        many = ["scripts/m%03d.py" % i for i in range(500)]
        # (class, paths, runners, the whole module section, the COMMAND RUNNERS names, [(import, unsafe() refuses it)])
        rows = [
            ("0 paths", [], (), [], ["none shown"], []),
            ("0 modules: docs, shell, tests only", ["docs/notes.md", "scripts/run.sh", "scripts/test_ok_mod.py",
                                                   "scripts/loop/test_x.py"], (), [], ["none shown"], []),
            ("1 module: the header still heads it", ["scripts/ok_mod.py"], (),
             [HEADER, "  scripts/ok_mod.py" + YES], ["none shown"], [("ok_mod", False)]),
            ("many modules, in path order, non modules skipped",
             ["scripts/bad_mod.py", "docs/a.md", "scripts/ok_mod.py", "scripts/test_ok_mod.py", "scripts/mytest_mod.py"],
             (), [HEADER, "  scripts/bad_mod.py" + NO, "  scripts/ok_mod.py" + YES, "  scripts/mytest_mod.py" + YES],
             ["none shown"], [("bad_mod", True), ("ok_mod", False), ("mytest_mod", False)]),
            ("a path given twice is one line", ["scripts/ok_mod.py", "scripts/ok_mod.py"], (),
             [HEADER, "  scripts/ok_mod.py" + YES], ["none shown"], []),
            ("scripts/<pkg>/__init__.py is judged as pkg: NOT importable", ["scripts/pkg/__init__.py"], (),
             [HEADER, "  scripts/pkg/__init__.py" + NO], ["none shown"], [("pkg", True), ("scripts.pkg", False)]),
            ("a package __init__ outside scripts/ is judged by its dotted package", ["plugin/core/__init__.py"], (),
             [HEADER, "  plugin/core/__init__.py" + YES], ["none shown"], [("plugin.core", False)]),
            ("a plugin module is judged by its dotted name, never its bare stem",
             ["plugin/core/good.py", "plugin/core/bad.py"], (),
             [HEADER, "  plugin/core/good.py" + YES, "  plugin/core/bad.py" + NO], ["none shown"], [("plugin.core.good", False), ("plugin.core.bad", True), ("good", True)]),
            ("a scripts/ module is judged by its bare stem too", ["scripts/socket.py"], (),
             [HEADER, "  scripts/socket.py" + NO], ["none shown"], [("socket", True), ("scripts.socket", False)]),
            ("a scripts/loop/ module is judged by its bare stem too (unsafe scripts/ twin)", ["scripts/loop/x.py"], (),
             [HEADER, "  scripts/loop/x.py" + NO], ["none shown"], [("x", True)]),
            ("a scripts/loop/ module whose stem resolves to itself", ["scripts/loop/lonely.py"], (),
             [HEADER, "  scripts/loop/lonely.py" + YES], ["none shown"], [("lonely", False)]),
            ("1 runner shown, a runner not shown is never named", ["scripts/ok_mod.py", "scripts/bad_mod.py"],
             ("scripts/bad_mod.py", "scripts/elsewhere.py"),
             [HEADER, "  scripts/ok_mod.py" + YES, "  scripts/bad_mod.py" + NO + " (command runner)"],
             ["scripts/bad_mod.py"], []),
            ("many runners in path order, a runner given twice named once",
             ["scripts/ok_mod.py", "scripts/bad_mod.py", "scripts/ok_mod.py"], ("scripts/ok_mod.py", "scripts/bad_mod.py"),
             [HEADER, "  scripts/ok_mod.py" + YES + " (command runner)", "  scripts/bad_mod.py" + NO + " (command runner)"],
             ["scripts/ok_mod.py", "scripts/bad_mod.py"], []),
            ("exactly 1 module cut: it is counted, never silently dropped",
             ["scripts/ok_mod.py", "scripts/bad_mod.py", long_path], (),
             [HEADER, "  scripts/ok_mod.py" + YES, "  scripts/bad_mod.py" + NO, TAIL % 1], ["none shown"], []),
            ("the cap counts BYTES: a line that fits in characters and not in bytes is cut",
             ["scripts/ok_mod.py", wide_path], (),
             [HEADER, "  scripts/ok_mod.py" + YES, TAIL % 1], ["none shown"], []),
            ("many cut: dropped from the end, counted", many, (), None, ["none shown"], []),
            ("scripts/subprocess.py with safe content is NOT importable: the grader refuses its bare stem",
             ["scripts/subprocess.py"], (), [HEADER, "  scripts/subprocess.py" + NO], ["none shown"], [("subprocess", True)]),
        ]
        for name, paths, runners, expect, runner_names, imports in rows:
            with self.subTest(name):
                G._REPO_SAFE.clear()
                text = G.screen_rules(list(paths), runners=set(runners))
                self.assertLessEqual(len(text.encode("utf-8")), G.SCREEN_CAP)
                self.assertTrue(text.startswith(G.SCREEN_HEAD + "\n"))
                self.assertEqual(_names(text, "COMMAND RUNNERS"), runner_names)
                got = self.section(text)
                if expect is not None:
                    self.assertEqual(got, expect)
                else:                                          # 500 modules: a prefix is listed, the rest counted
                    self.assertEqual(got[0], HEADER)
                    listed = [l.strip().split(":")[0] for l in got[1:-1]]
                    self.assertTrue(listed)
                    self.assertEqual(listed, paths[:len(listed)])
                    self.assertEqual(got[-1], TAIL % (len(paths) - len(listed)))
                for mod, refused in imports:                   # the verdict is the grader's: unsafe() agrees
                    G._REPO_SAFE.clear()
                    why = G.unsafe(_build("import %s\n" % mod, path="scripts/other.py"), runners=set())
                    self.assertEqual(why is not None, refused, (mod, why))


class TheSwitchParser(unittest.TestCase):
    def test_mode_parser_defaults_to_off(self):
        self.assertEqual(G.brief_screen_mode({}), "off")
        self.assertEqual(G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": ""}), "off")

    def test_mode_parser_reads_on_and_off_after_strip_and_lower(self):
        self.assertEqual(G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": "on"}), "on")
        self.assertEqual(G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": " ON "}), "on")
        self.assertEqual(G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": "off"}), "off")
        self.assertEqual(G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": "Off\n"}), "off")

    def test_mode_parser_refuses_other_values(self):
        for bad in ("yes", "1", "true", "o n"):
            with self.assertRaises(ValueError) as cm:
                G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": bad})
            self.assertIn("BROTHER_BRIEF_SCREEN", str(cm.exception))

    def test_mode_parser_refuses_a_value_that_is_not_text_by_name(self):
        for bad in (1, ["on"], b"on"):
            with self.assertRaises(ValueError, msg=repr(bad)) as cm:
                G.brief_screen_mode({"BROTHER_BRIEF_SCREEN": bad})
            self.assertIn("BROTHER_BRIEF_SCREEN", str(cm.exception))

    def test_mode_parser_reads_the_real_environment_when_none_is_given(self):
        old = os.environ.pop("BROTHER_BRIEF_SCREEN", None)
        self.addCleanup(lambda: os.environ.__setitem__("BROTHER_BRIEF_SCREEN", old) if old is not None
                        else os.environ.pop("BROTHER_BRIEF_SCREEN", None))
        self.assertEqual(G.brief_screen_mode(), "off")
        os.environ["BROTHER_BRIEF_SCREEN"] = "on"
        self.assertEqual(G.brief_screen_mode(), "on")


if __name__ == "__main__":
    unittest.main()
