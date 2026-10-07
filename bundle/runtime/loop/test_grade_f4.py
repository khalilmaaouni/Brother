#!/usr/bin/env python3
"""RR lane F4: a grader PASS means the SUBMITTED bytes made a REAL failing test pass, never less.

The modules under test are scripts/loop/grade_build.py and spec_check.py (the wrappers and spec_accept are in
test_grade_wrappers_f4.py beside this file). Each class answers one finding of the Codex audit of 2026-09-27
(F-grading-and-build, findings 1, 2, 3 and 5). The grader is driven at its ENTRY POINT, as the loop drives it: a tiny git repository, a build json, the real
grade_build.py run as a child. Every failure fixture trips ONE guard, so deleting that guard turns exactly its test red;
every refusal sits beside a control that must still PASS, so a grader that refuses everything is red too.

Run: python3 -B scripts/loop/test_grade_f4.py   (the grader children run with BROTHER_SANDBOX=off: this suite is about
the verdict, and scripts/test_grade_build_guard.py owns the sandbox profile)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grade_build as G  # noqa: E402

GRADER = os.path.join(HERE, "grade_build.py")
MODERN = shutil.which("python3") or sys.executable   # the grader refuses to run AS the old interpreter (one version, not two)

CALC = "def double(x):\n    return x * 2\n\n\ndef triple(x):\n    return x * 3\n\n\nLIMIT = 10\n"
CALC_TEST_BODY = ("class Calc(unittest.TestCase):\n"
                  "    def test_double(self):\n        self.assertEqual(double(2), 4)\n        self.assertEqual(double(-3), -6)\n\n"
                  "    def test_triple(self):\n        self.assertEqual(triple(2), 6)\n\n"
                  "    def test_limit(self):\n        self.assertEqual(LIMIT, 10)\n")
IMPORT_CALC = "from pkg.calc import double, triple, LIMIT\n"
CALC_MUTATIONS = [{"name": "double off", "path": "pkg/calc.py", "find": "return x * 2", "replace": "return x * 4"},
                  {"name": "triple off", "path": "pkg/calc.py", "find": "return x * 3", "replace": "return x + 3"},
                  {"name": "limit off", "path": "pkg/calc.py", "find": "LIMIT = 10", "replace": "LIMIT = 11"}]
SELF = "_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calc.py')\n"


def calc_build(test_head, calc=CALC, mutations=None, extra_edits=(), unknowns=None):
    """A new module pkg/calc.py and its test pkg/test_calc.py: `test_head` is the test module's top, before the tests."""
    build = {"edits": [{"path": "pkg/calc.py", "new_file_content": calc}] + list(extra_edits),
             "tests": [{"path": "pkg/test_calc.py", "new_file_content": test_head + "\n\n" + CALC_TEST_BODY}],
             "done_check": "python3 -B -m unittest pkg.test_calc",
             "mutations": mutations or CALC_MUTATIONS}
    if unknowns is not None:
        build["unknowns"] = unknowns
    return build


class Repo(object):
    """One git repository, one sandbox folder and one HOME for a whole class: the grader's slot sandboxes are reused and
    reset per grade, exactly as the loop reuses them, so each grade costs its runs and not a clone."""

    def __init__(self):
        self.d = tempfile.mkdtemp(prefix="grade-f4-")
        self.repo = os.path.join(self.d, "repo")
        self.outside = os.path.join(self.d, "outside")
        self.home = os.path.join(self.d, "home")
        self.sandboxes = os.path.join(self.d, "sandboxes")
        for p in (os.path.join(self.repo, "pkg"), os.path.join(self.repo, "scripts"),
                  os.path.join(self.repo, "docs", "plan"), self.outside, self.home):
            os.makedirs(p)
        files = {"pkg/__init__.py": "", "pkg/m.py": "def f(x):\n    return None\n", "scripts/README": "tests beside\n",
                 "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json": '{"units": []}'}
        for rel, text in files.items():
            with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        self.victim = os.path.join(self.outside, "victim.py")
        with open(self.victim, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        os.symlink(self.outside, os.path.join(self.repo, "outlink"))   # committed: a directory symlink out of the tree
        for argv in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            self.git(*argv)

    def git(self, *argv):
        return subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
                              + list(argv), cwd=self.repo, capture_output=True, text=True, check=True, env=self.env())

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER_"))}
        env.update(HOME=self.home, BROTHER_SANDBOX="off", BROTHER_GRADE_SANDBOXES=self.sandboxes, PYTHONDONTWRITEBYTECODE="1")
        return env

    def grade(self, build, name):
        path = os.path.join(self.d, name + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(build, fh)
        r = subprocess.run([MODERN, "-B", GRADER, path], cwd=self.repo, capture_output=True, text=True, env=self.env(), timeout=600)
        return r.returncode, r.stdout, r.stdout + r.stderr

    def close(self):
        for s in os.listdir(self.sandboxes) if os.path.isdir(self.sandboxes) else []:
            subprocess.run(  # sbe: allow-silent test teardown of this fixture's own scratch sandboxes; one already gone must not fail the test
                ["git", "-C", self.repo, "worktree", "remove", "--force", os.path.join(self.sandboxes, s)],
                capture_output=True, env=self.env())
        shutil.rmtree(self.d, ignore_errors=True)


class GraderCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.R = Repo()

    @classmethod
    def tearDownClass(cls):
        cls.R.close()

    def verdict(self, out):
        lines = [line for line in out.splitlines() if line.startswith(("PASS", "FAIL"))]
        return lines[-1] if lines else ""

    def assertPass(self, build, name):
        rc, out, full = self.R.grade(build, name)
        self.assertEqual((rc, self.verdict(out)), (0, "PASS"), full)
        return out

    def assertRefused(self, build, name, reason):
        rc, out, full = self.R.grade(build, name)
        self.assertEqual(rc, 1, full)
        self.assertTrue(self.verdict(out).startswith("FAIL: "), full)
        self.assertIn(reason, self.verdict(out), full)
        return out


class ATestThatRanAndFailedIsTheOnlyEvidence(GraderCase):
    """Codex audit F1: exit codes alone decided red, green and every mutation."""

    def test_control_a_new_module_whose_test_cannot_import_it_without_the_code_passes(self):
        out = self.assertPass(calc_build("import unittest\n" + IMPORT_CALC), "new-module")
        self.assertIn("RED-WITHOUT-CODE   yes (the tests cannot import pkg.calc, which this build writes)", out)
        self.assertIn("MUTATIONS          3 of 3 applied were caught", out)

    def test_control_an_edit_whose_test_runs_and_fails_without_the_code_passes(self):
        build = {"edits": [{"path": "pkg/m.py", "find": "return None", "replace": "return x + 1"}],
                 "tests": [{"path": "pkg/test_m.py", "new_file_content": "import unittest\nfrom pkg import m\n\n\nclass T(unittest.TestCase):\n"
                            "    def test_f(self):\n        self.assertEqual(m.f(1), 2)\n        self.assertEqual(m.f(5), 6)\n"}],
                 "done_check": "python3 -B -m unittest pkg.test_m",
                 "mutations": [{"name": "plus two", "path": "pkg/m.py", "find": "return x + 1", "replace": "return x + 2"},
                               {"name": "zero", "path": "pkg/m.py", "find": "return x + 1", "replace": "return 0"},
                               {"name": "minus", "path": "pkg/m.py", "find": "return x + 1", "replace": "return x - 1"}]}
        out = self.assertPass(build, "edit")
        self.assertRegex(out, r"RED-WITHOUT-CODE   yes \(1 of 1 test\(s\) failed\)")

    def test_a_test_file_with_no_runner_executes_nothing_and_is_refused(self):
        # the Codex shape: a unittest file without unittest.main() exits 0 having run nothing
        build = {"edits": [{"path": "audit_subject.py", "new_file_content": "def answer():\n    return 42\n"}],
                 "tests": [{"path": "scripts/test_audit.py", "new_file_content": "import sys, unittest\nsys.path.insert(0, '.')\n"
                            "from audit_subject import answer\n\n\nclass T(unittest.TestCase):\n    def test_answer(self):\n"
                            "        self.assertEqual(answer(), 42)\n"}],
                 "done_check": "python3 -B scripts/test_audit.py",
                 "mutations": [{"name": "n%d" % i, "path": "audit_subject.py", "find": "return 42", "replace": "return %d" % i}
                               for i in (41, 40, 39)]}
        out = self.assertRefused(build, "no-runner", "a test command of this build executed no test")
        self.assertIn("RED-WITHOUT-CODE   yes", out)

    def test_a_suite_whose_every_test_is_skipped_executes_nothing_and_is_refused(self):
        head = "import unittest\n" + IMPORT_CALC + "\n\n@unittest.skip('never runs')"
        out = self.assertRefused(calc_build(head), "all-skipped", "a test command of this build executed no test")
        self.assertIn("0 test(s) executed", out)

    def test_a_suite_that_runs_nothing_on_the_old_interpreter_is_refused_there(self):
        head = "import sys, unittest\n" + IMPORT_CALC + "\n\n@unittest.skipIf(sys.version_info < (3, 10), 'modern only')"
        out = self.assertRefused(calc_build(head), "old-skipped", "suite not green on /usr/bin/python3")
        self.assertIn("GREEN-WITH-CODE    yes", out)

    def test_a_red_run_on_an_import_this_build_does_not_write_is_the_wrong_reason(self):
        head = ("import unittest\ntry:\n    " + IMPORT_CALC.strip() + "\nexcept ImportError:\n"
                "    from json import zz_written_by_nobody  # noqa: F401\n")   # the screen allows json; the name is nobody's
        out = self.assertRefused(calc_build(head), "wrong-import", "tests fail without the code for no reason a test states")
        self.assertIn("zz_written_by_nobody", out)

    def test_a_red_run_that_crashed_before_any_test_is_the_wrong_reason(self):
        head = "import os, unittest\n" + SELF + "SOURCE = open(_p).read()\n" + IMPORT_CALC
        self.assertRefused(calc_build(head), "crash-red", "tests fail without the code for no reason a test states")

    def test_a_mutation_that_only_stops_the_module_importing_is_not_caught(self):
        renames = [{"name": "rename " + n, "path": "pkg/calc.py", "find": f, "replace": f.replace(n, n + "_")}
                   for n, f in (("double", "def double(x):"), ("triple", "def triple(x):"), ("LIMIT", "LIMIT = 10"))]
        out = self.assertRefused(calc_build("import unittest\n" + IMPORT_CALC, mutations=renames), "import-mutations",
                                 "mutations: 3 applied, 0 caught")
        self.assertIn("SURVIVED: exit 1 but no test ran and failed", out)


class TheVerdictIsAboutTheSubmittedBytes(GraderCase):
    """Codex audit F2: a test rewrote the implementation during its green run and the corrected file was graded."""

    def test_the_codex_shape_a_test_that_corrects_the_code_it_grades_is_refused(self):
        head = ("import os, unittest\n" + SELF + "if os.path.exists(_p) and 'x * 0' in open(_p).read():\n"
                "    open(_p, 'w').write(open(_p).read().replace('x * 0', 'x * 2'))\n" + IMPORT_CALC)
        self.assertRefused(calc_build(head, calc=CALC.replace("x * 2", "x * 0"), mutations=CALC_MUTATIONS[1:] + [
            {"name": "double again", "path": "pkg/calc.py", "find": "return x * 0", "replace": "return x * 5"}]),
            "other-bytes", "a test run rewrote this build's own file(s) pkg/calc.py")

    def test_a_rewrite_during_the_green_run_alone_is_refused(self):
        # modern Python touches the submitted bytes, the old one puts them back: only the check after green can see it
        head = ("import os, sys, unittest\n" + SELF + "SUBMITTED = %r\nif os.path.exists(_p):\n    _s = open(_p).read()\n"
                "    if sys.version_info >= (3, 10) and _s == SUBMITTED:\n        open(_p, 'w').write(_s + '# touched\\n')\n"
                "    elif sys.version_info < (3, 10) and _s == SUBMITTED + '# touched\\n':\n        open(_p, 'w').write(SUBMITTED)\n"
                % CALC) + IMPORT_CALC
        self.assertRefused(calc_build(head), "green-transient", "a test run rewrote this build's own file(s) pkg/calc.py")

    def test_a_rewrite_undone_by_the_next_command_of_the_same_run_is_refused(self):
        # two own commands (the test item and a -v done check): the first grades touched bytes, the second puts them back,
        # so the file reads as submitted once the whole run is over; only a check after EVERY command sees it
        head = ("import os, unittest\n" + SELF + "SUBMITTED = %r\nif os.path.exists(_p):\n    _s = open(_p).read()\n"
                "    if _s in (SUBMITTED, SUBMITTED + '# touched\\n'):\n"
                "        open(_p, 'w').write(_s + '# touched\\n' if _s == SUBMITTED else SUBMITTED)\n" % CALC) + IMPORT_CALC
        # a mutated file is left alone, so no mutation run sees a change: the per command check is the only one that can
        build = calc_build(head)
        build["done_check"] = "python3 -B -m unittest pkg.test_calc -v"
        self.assertRefused(build, "within-run", "a test run rewrote this build's own file(s) pkg/calc.py")

    def test_a_rewrite_during_the_old_interpreter_leg_is_refused(self):
        head = ("import os, sys, unittest\n" + SELF + "if os.path.exists(_p) and sys.version_info < (3, 10) and "
                "'# touched' not in open(_p).read():\n    open(_p, 'a').write('# touched\\n')\n") + IMPORT_CALC
        self.assertRefused(calc_build(head), "old-leg", "a test run rewrote this build's own file(s) pkg/calc.py")

    def test_a_rewrite_of_another_build_file_during_the_last_mutation_is_refused(self):
        extra = {"path": "pkg/extra.py", "new_file_content": "EXTRA = 1\n"}
        head = ("import os, unittest\n" + SELF + "if os.path.exists(_p) and 'LIMIT = 11' in open(_p).read():\n"
                "    open(os.path.join(os.path.dirname(_p), 'extra.py'), 'w').write('EXTRA = 2\\n')\n" + IMPORT_CALC)
        out = self.assertRefused(calc_build(head, extra_edits=[extra]), "mutation-run", "a test run rewrote this build's own file(s) pkg/extra.py")
        self.assertNotIn("mutations:", self.verdict(out), "a run on other bytes gets ONE reason, not a count as well")

    def test_a_test_that_rewrites_itself_in_the_run_without_the_code_is_refused(self):
        head = ("import os, unittest\n" + SELF + "if not os.path.exists(_p):\n"
                "    open(os.path.abspath(__file__), 'a').write('# rewritten\\n')\n" + IMPORT_CALC)
        self.assertRefused(calc_build(head), "red-rewrite", "a test run rewrote this build's own file(s) pkg/test_calc.py")


class OneLineIsOneLineWhoeverWroteIt(GraderCase):
    """Codex audit F3: the worker's unknowns field printed a standalone PASS above the real FAIL."""

    def test_worker_text_cannot_print_a_verdict_line(self):
        build = calc_build("import unittest\n")   # tests that pass without the code: a FAIL
        build["tests"][0]["new_file_content"] = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_t(self):\n        pass\n"
        build["unknowns"] = "worker text\nPASS\n\u2028PASS"
        rc, out, full = self.R.grade(build, "unknowns")
        self.assertEqual(rc, 1, full)
        self.assertEqual([line for line in out.splitlines() if line.startswith(("PASS", "FAIL"))],
                         ["FAIL: tests pass without the code"], full)
        self.assertIn("UNKNOWNS           worker text\\x0aPASS\\x0a\\u2028PASS", out)

    def test_a_mutation_name_cannot_print_a_line_either(self):
        muts = [dict(CALC_MUTATIONS[0], name="off\nFAIL: forged")] + CALC_MUTATIONS[1:]
        out = self.assertPass(calc_build("import unittest\n" + IMPORT_CALC, mutations=muts), "mutation-name")
        self.assertNotIn("\nFAIL: forged", out)

    def test_every_line_of_the_verdict_goes_through_say(self):
        import ast
        with open(GRADER, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("main", "_main")):
            prints = [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "print"]
            self.assertEqual(prints, [], "%s prints around say() at line(s) %s" % (fn.name, prints))

    def test_one_line_escapes_every_line_break_any_reader_splits_on(self):
        text = "a\nb\rc\x0bd\x0ce\x1cf\x85g\u2028h\u2029i\x00j"
        self.assertEqual(len(G.one_line(text).splitlines()), 1)
        self.assertEqual(G.one_line("plain text: 1 | 2"), "plain text: 1 | 2")


class PatchesNeverWriteThroughASymlink(unittest.TestCase):
    """Codex audit F5: apply() checked the lexical path and a directory symlink carried the write out of the tree."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="grade-f4-root-")
        self.outside = tempfile.mkdtemp(prefix="grade-f4-outside-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.outside, True)
        self.victim = os.path.join(self.outside, "victim.py")
        with open(self.victim, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")

    def apply(self, item):
        problems = []
        n = G.apply(self.root, [item], problems, "edit")
        return n, problems

    def test_a_new_file_under_a_directory_symlink_out_of_the_tree_is_refused(self):
        os.symlink(self.outside, os.path.join(self.root, "link"))
        n, problems = self.apply({"path": "link/marker.txt", "new_file_content": "escaped"})
        self.assertEqual((n, len(problems)), (0, 1), problems)
        self.assertFalse(os.path.exists(os.path.join(self.outside, "marker.txt")))

    def test_an_edit_of_a_file_reached_through_a_directory_symlink_is_refused(self):
        os.symlink(self.outside, os.path.join(self.root, "link"))
        n, problems = self.apply({"path": "link/victim.py", "find": "VALUE = 1", "replace": "VALUE = 2"})
        self.assertEqual(n, 0, problems)
        with open(self.victim, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "VALUE = 1\n")

    def test_a_file_symlink_is_never_written_through_even_when_it_points_inside(self):
        with open(os.path.join(self.root, "real.py"), "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        os.symlink(os.path.join(self.root, "real.py"), os.path.join(self.root, "alias.py"))
        n, problems = self.apply({"path": "alias.py", "find": "VALUE = 1", "replace": "VALUE = 2"})
        self.assertEqual(n, 0, problems)
        self.assertIn("symlink", problems[0])

    def test_a_dangling_symlink_is_not_a_free_name_for_a_new_file(self):
        os.symlink(os.path.join(self.outside, "not-yet.py"), os.path.join(self.root, "new.py"))
        n, problems = self.apply({"path": "new.py", "new_file_content": "x = 1\n"})
        self.assertEqual(n, 0, problems)
        self.assertFalse(os.path.exists(os.path.join(self.outside, "not-yet.py")))

    def test_the_git_pointer_file_at_the_root_is_refused(self):
        n, problems = self.apply({"path": ".git", "find": "gitdir", "replace": "x"})
        self.assertEqual(n, 0, problems)
        self.assertIn("escapes the tree", problems[0])

    def test_control_a_real_nested_folder_inside_the_tree_is_written(self):
        n, problems = self.apply({"path": "a/b/new.py", "new_file_content": "x = 1\n"})
        self.assertEqual((n, problems), (1, []))

    def test_a_file_swapped_for_a_link_to_the_same_bytes_reads_as_changed(self):
        with open(os.path.join(self.root, "calc.py"), "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        before = G.fingerprint(self.root, ["calc.py"])
        os.remove(os.path.join(self.root, "calc.py"))
        os.symlink(self.victim, os.path.join(self.root, "calc.py"))
        self.assertEqual(G.changed(self.root, before), ["calc.py"])


class AMutationPathOutOfTheTreeIsNeverTouched(GraderCase):
    def test_a_mutation_through_a_committed_directory_symlink_does_not_apply_and_writes_nothing(self):
        st = os.stat(self.R.victim)
        muts = CALC_MUTATIONS + [{"name": "escape", "path": "outlink/victim.py", "find": "VALUE = 1", "replace": "VALUE = 2"}]
        out = self.assertPass(calc_build("import unittest\n" + IMPORT_CALC, mutations=muts), "mutation-escape")
        self.assertIn("escape", [line.split()[1] for line in out.splitlines() if "DID NOT APPLY" in line])
        after = os.stat(self.R.victim)
        self.assertEqual((after.st_mtime_ns, after.st_size), (st.st_mtime_ns, st.st_size), "a mutation wrote out of the tree")


class TheRunnersOwnSummaryDecides(unittest.TestCase):
    """test_verdict on the exact shapes both interpreters print."""
    SUMMARY = "----------------------------------------------------------------------\nRan %d test%s in 0.004s\n\n%s\n"

    def s(self, n, verdict):
        return self.SUMMARY % (n, "" if n == 1 else "s", verdict)

    def test_the_four_kinds(self):
        loader = ("E\n======================================================================\n"
                  "ERROR: test_x (unittest.loader._FailedTest.test_x)\n----\nImportError: Failed to import test module: test_x\n"
                  "Traceback (most recent call last):\nImportError: cannot import name 'newmod' from 'pkg' (/x/pkg/__init__.py)\n")
        self.assertEqual(G.test_verdict(0, self.s(3, "OK")), ("PASSED", "3 test(s) ran"))
        self.assertEqual(G.test_verdict(1, "FAIL: test_a (m.T.test_a)\n" + self.s(2, "FAILED (failures=1)"))[0], "FAILED")
        self.assertEqual(G.test_verdict(1, loader + self.s(1, "FAILED (errors=1)")), ("IMPORT", "pkg.newmod"))
        self.assertEqual(G.test_verdict(1, "Traceback\nModuleNotFoundError: No module named 'audit_subject'\n"), ("IMPORT", "audit_subject"))
        for code, out in ((0, ""), (0, self.s(0, "OK")), (0, self.s(2, "OK (skipped=2)")), (5, self.s(0, "NO TESTS RAN")),
                          (0, self.s(1, "OK") + self.s(1, "OK")), (1, "Traceback\nFileNotFoundError: x\n"), (1, self.s(1, "OK"))):
            self.assertEqual(G.test_verdict(code, out)[0], "NONE", (code, out))

    def test_red_needs_a_failing_test_or_this_builds_own_import(self):
        mods = G.supplied({"edits": [{"path": "scripts/loop/newmod.py"}, {"path": "pkg/sub/__init__.py"}, {"path": "doc.md"}]})
        self.assertEqual(mods, {"scripts.loop.newmod", "pkg.sub"})
        imp = lambda name: "ModuleNotFoundError: No module named '%s'\n" % name
        for name in ("newmod", "loop.newmod", "scripts.loop.newmod", "pkg.sub", "pkg"):
            self.assertTrue(G.red_reason(1, imp(name), mods), name)
        self.assertTrue(G.red_reason(1, "ImportError: cannot import name 'f' from 'newmod' (x)\n", mods))
        for name in ("zz_other", "json_other"):
            self.assertEqual(G.red_reason(1, imp(name), mods), "", name)
        self.assertEqual(G.red_reason(1, "Traceback\nFileNotFoundError: x\n", mods), "")


class TheSpecsOwnCheckMustRunATest(unittest.TestCase):
    """Codex audit F1, the landing side: spec_check accepted an EMPTY test script as green on both interpreters."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="spec-check-f4-")
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, "scripts"))
        import spec_check
        self.S = spec_check

    def check(self, body):
        with open(os.path.join(self.d, "scripts", "test_x.py"), "w", encoding="utf-8") as fh:
            fh.write(body)
        return self.S.run_both("python3 -B scripts/test_x.py", self.d, timeout=120)

    def test_an_empty_script_is_not_green(self):
        self.assertFalse(self.check("")[0])

    def test_a_suite_with_no_test_is_not_green(self):
        self.assertFalse(self.check("import unittest\nif __name__ == '__main__':\n    unittest.main()\n")[0])

    def test_control_a_suite_that_runs_one_passing_test_is_green(self):
        ok, why = self.check("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_t(self):\n        self.assertTrue(True)\n\n\n"
                             "if __name__ == '__main__':\n    unittest.main()\n")
        self.assertTrue(ok, why)

    # The spec's check runs inside the grader's sandbox (review 2026-10-02: it ran on the open host). Each test below
    # plants ONE escape and reads the entry point the landing calls, run_both.
    def test_a_check_that_writes_outside_the_tree_writes_nothing_and_is_not_green(self):
        outside = tempfile.mkdtemp(prefix="spec-check-f4-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        target = os.path.join(outside, "escape.txt")
        ok, why = self.check("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_t(self):\n        open(%r, 'w').write('escaped')\n\n\n"
                             "if __name__ == '__main__':\n    unittest.main()\n" % target)
        self.assertFalse(ok, "the spec's check wrote outside the landing tree and was called green: %s" % why)
        self.assertFalse(os.path.exists(target), "the spec's check wrote outside the landing tree")

    def test_a_credential_shaped_name_is_not_seen_by_the_check(self):
        os.environ["SPEC_CHECK_F4_TOKEN"] = "planted"   # the NAME is the test; the value is never printed
        self.addCleanup(os.environ.pop, "SPEC_CHECK_F4_TOKEN", None)
        ok, why = self.check("import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n    def test_t(self):\n"
                             "        self.assertNotIn('SPEC_CHECK_F4_TOKEN', os.environ)\n\n\nif __name__ == '__main__':\n    unittest.main()\n")
        self.assertTrue(ok, why)


if __name__ == "__main__":
    unittest.main()
