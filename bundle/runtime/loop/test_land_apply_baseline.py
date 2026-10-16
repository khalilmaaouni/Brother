#!/usr/bin/env python3
"""G10: the landing gate judges every red suite against the UNCHANGED copy of the tree, so a red the build did not cause
is never charged to it.

WHY. Measured 2026-10-14: a no-op build of an unchanged file was refused with 16 failures and 36 errors because a sibling
suite cannot pass inside the landing sandbox even on unchanged code; on repeat the unit was parked. Every case below runs
scripts/loop/land_apply.py AS A COMMAND (its real entry point, the way land_batch runs it) in a fixture git repository,
one build per case, and reads its exit code and stdout. Each fixture isolates ONE outcome:
  env     scripts/test_a.py is red on both copies: environment defect, VERDICT BLOCKED-ENVIRONMENT, exit 3, nothing lands
  build   scripts/test_b.py is green on the base and red on the build: VERDICT RED, exit 1, exactly as before G10
  green   scripts/test_c.py is green on both: VERDICT SUITES GREEN, exit 0, the base copy is never made
  nodata  scripts/test_d.py kills itself on the base and is red on the build: VERDICT NO-DATA, exit 4, nothing lands
The one base run per suite and interpreter is checked at main() in process, counting runs in the unchanged copy.
Hermetic: every tree, HOME and TMPDIR is a temporary directory; nothing is written into the repository.
"""
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
LANDER = os.path.join(HERE, "land_apply.py")
sys.path.insert(0, HERE)
import land_apply  # noqa: E402
import land_batch  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1")
RED = "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail('red')\nif __name__ == '__main__':\n    unittest.main()\n"
GREEN = "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\nif __name__ == '__main__':\n    unittest.main()\n"
SAYS_ONE = ("import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport b\n"
            "class T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(b.VALUE, 1)\n"
            "if __name__ == '__main__':\n    unittest.main()\n")
DIES_ON_BASE = ("import os, signal, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport d\n"
                "if d.MODE == 'base':\n    os.kill(os.getpid(), signal.SIGKILL)\n"
                "class T(unittest.TestCase):\n    def test_x(self):\n        self.fail('red on the build')\n"
                "if __name__ == '__main__':\n    unittest.main()\n")
FILES = {"scripts/a.py": "VALUE = 1\n", "scripts/test_a.py": RED,
         "scripts/b.py": "VALUE = 1\n", "scripts/test_b.py": SAYS_ONE,
         "scripts/c.py": "VALUE = 1\n", "scripts/test_c.py": GREEN,
         "scripts/d.py": "MODE = 'base'\n", "scripts/test_d.py": DIES_ON_BASE}
EDIT = {"a": ("VALUE = 1", "VALUE = 2"), "b": ("VALUE = 1", "VALUE = 2"), "c": ("VALUE = 1", "VALUE = 2"),
        "d": ("MODE = 'base'", "MODE = 'build'")}


class Tree:
    """A fixture repository at one commit holding FILES, a HOME and a TMPDIR of its own, and build JSONs that edit them."""

    def __init__(self, test):
        self.dir = tempfile.mkdtemp(prefix="g10-tree-")
        self.home = tempfile.mkdtemp(prefix="g10-home-")
        os.makedirs(os.path.join(self.home, "tmp"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        test.addCleanup(shutil.rmtree, self.home, True)
        for rel, text in FILES.items():
            os.makedirs(os.path.dirname(os.path.join(self.dir, rel)), exist_ok=True)
            with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        for argv in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"]):
            subprocess.run(["git", "-C", self.dir] + argv, check=True, env=GIT_ENV, capture_output=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER_"))}
        self.env.update(HOME=self.home, TMPDIR=os.path.join(self.home, "tmp"), GIT_CONFIG_NOSYSTEM="1")

    def build(self, *letters):
        """A build JSON (outside the tree) that edits scripts/<letter>.py for each letter; each sibling suite then runs."""
        p = os.path.join(self.home, "build-%s.json" % "".join(letters))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"edits": [{"path": "scripts/%s.py" % x, "find": EDIT[x][0], "replace": EDIT[x][1]} for x in letters],
                       "tests": []}, fh)
        return p

    def land(self, *letters):
        """(exit code, stdout) of the lander run as a command from the tree root, killed by group past 300 s."""
        p = subprocess.Popen([sys.executable, "-B", LANDER, self.build(*letters)], cwd=self.dir, env=self.env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            out, err = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL); p.communicate()
            raise AssertionError("the lander did not finish within 300 s")
        return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    def text(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as fh:
            return fh.read()


def verdict(out):
    return [l for l in out.splitlines() if l.startswith("VERDICT ")]


class EntryPoint(unittest.TestCase):
    def setUp(self):
        self.tree = Tree(self)

    def assertUnlanded(self, letter):
        self.assertEqual(self.tree.text("scripts/%s.py" % letter), FILES["scripts/%s.py" % letter], "the landing tree was written")

    def assertNeverLands(self, rc, out, err):
        """land_batch's own reader (after_build) never reads this run as a landing."""
        # G14: an environment defect or a NO-DATA base run is a "hold" (no note, no count, no park); a build red is a
        # "drop". Either way it never lands.
        self.assertIn(land_batch.after_build(rc, out, "READY x", err)[0], ("drop", "hold"), out)

    def test_red_on_both_copies_is_an_environment_defect_never_the_builds(self):
        rc, out, err = self.tree.land("a")
        self.assertEqual(rc, 3, out + err)
        self.assertEqual(len(verdict(out)), 1, out)
        self.assertTrue(verdict(out)[0].startswith("VERDICT BLOCKED-ENVIRONMENT | environment defect: scripts/test_a.py on py3 (base exit 1, build exit 1)"), out)
        self.assertEqual(out.strip().splitlines()[-1], verdict(out)[0], "the verdict is the last line land_batch reads")
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("ENVDEF  environment defect: scripts/test_a.py")]), 2, out)
        self.assertEqual([l for l in out.splitlines() if l.startswith("REMEDY  ")][:1] != [], True, out)
        self.assertNotIn("VERDICT RED", out); self.assertNotIn("SUITES GREEN", out)
        self.assertUnlanded("a"); self.assertNeverLands(rc, out, err)

    def test_red_only_on_the_built_copy_is_the_builds_red_as_before(self):
        rc, out, err = self.tree.land("b")
        self.assertEqual(rc, 1, out + err)
        self.assertEqual(verdict(out), ["VERDICT RED: 2 | touched: scripts/b.py"], out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("BASE    scripts/test_b.py") and "green on the unchanged copy" in l]), 2, out)
        self.assertNotIn("ENVDEF", out); self.assertNotIn("NO-DATA", out)
        self.assertUnlanded("b"); self.assertNeverLands(rc, out, err)

    def test_green_on_both_lands_and_never_makes_the_base_copy(self):
        rc, out, err = self.tree.land("c")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(verdict(out), ["VERDICT SUITES GREEN | touched: scripts/c.py"], out)
        self.assertFalse([l for l in out.splitlines() if l.startswith(("BASE", "ENVDEF", "NO-DATA", "REMEDY"))], out)
        self.assertEqual(self.tree.text("scripts/c.py"), "VALUE = 2\n")
        self.assertEqual(land_batch.after_build(rc, out, "READY x", err)[0], "land", out)

    def test_a_base_run_that_cannot_complete_is_no_data_never_a_pass(self):
        rc, out, err = self.tree.land("d")
        self.assertEqual(rc, 4, out + err)
        self.assertEqual(len(verdict(out)), 1, out)
        self.assertTrue(verdict(out)[0].startswith("VERDICT NO-DATA | base run did not complete: scripts/test_d.py on py3 (base exit "), out)
        self.assertNotIn("SUITES GREEN", out); self.assertNotIn("ENVDEF", out); self.assertNotIn("VERDICT RED", out)
        self.assertUnlanded("d"); self.assertNeverLands(rc, out, err)

    def test_the_builds_own_red_outranks_an_environment_defect(self):
        rc, out, err = self.tree.land("a", "b")
        self.assertEqual(rc, 1, out + err)
        self.assertTrue(verdict(out)[0].startswith("VERDICT RED: 2 |"), out)
        self.assertIn("ENVDEF  environment defect: scripts/test_a.py", out)
        self.assertUnlanded("a"); self.assertUnlanded("b")

    def test_no_data_outranks_an_environment_defect(self):
        rc, out, err = self.tree.land("a", "d")
        self.assertEqual(rc, 4, out + err)
        self.assertTrue(verdict(out)[0].startswith("VERDICT NO-DATA |"), out)
        self.assertUnlanded("a"); self.assertUnlanded("d")


class OneBaseRun(unittest.TestCase):
    def test_one_base_run_per_suite_and_interpreter_at_main(self):
        """main() in process with both interpreter passes on ONE interpreter: the suite is red in both passes, and the
        unchanged copy runs it once."""
        tree = Tree(self)
        roots, base_runs, real_copy, real_run = [], [], land_batch.make_copy, land_batch.run_group

        def copy(*a, **k):
            roots.append(real_copy(*a, **k)); return roots[-1]

        def run(cmd, timeout, env, cwd=None, stdin=None):
            if len(roots) > 1 and cwd == roots[1]: base_runs.append(cmd)
            return real_run(cmd, timeout, env, cwd=cwd, stdin=stdin)
        old = os.getcwd(); buf = io.StringIO()
        env = dict(tree.env)
        try:
            os.chdir(tree.dir)
            with unittest.mock.patch.object(land_batch, "make_copy", copy), unittest.mock.patch.object(land_batch, "run_group", run), \
                    unittest.mock.patch.object(sys, "argv", ["land_apply.py", tree.build("a")]), \
                    unittest.mock.patch.object(sys, "executable", "/usr/bin/python3"), \
                    unittest.mock.patch.dict(os.environ, env, clear=True), redirect_stdout(buf):
                rc = land_apply.main()
        finally:
            os.chdir(old)
        out = buf.getvalue()
        self.assertEqual(rc, 3, out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("ENVDEF")]), 2, out)
        self.assertEqual(len(base_runs), 1, "the unchanged copy ran the suite %d times: %s" % (len(base_runs), out))


class BaseRed(unittest.TestCase):
    """The one classifier's incomplete branches that the entry point fixtures do not reach."""

    def test_timeout_sandbox_refusal_and_no_copy_are_no_data(self):
        root = tempfile.mkdtemp(prefix="g10-root-"); self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "scripts"))
        with open(os.path.join(root, "scripts", "test_x.py"), "w", encoding="utf-8") as fh:
            fh.write(RED)
        done = lambda rc: (lambda *a, **k: subprocess.CompletedProcess([], rc, "", ""))
        self.assertEqual(land_apply.base_red(root, "scripts/test_x.py", sys.executable, root, {}, run=done(124)), (None, 124))
        self.assertEqual(land_apply.base_red(root, "scripts/test_x.py", sys.executable, root, {}, run=done(137)), (None, 137))
        self.assertEqual(land_apply.base_red(root, "scripts/test_x.py", sys.executable, root, {}, run=done(1)), (True, 1))
        self.assertEqual(land_apply.base_red(root, "scripts/test_x.py", sys.executable, root, {}, run=done(0)), (False, 0))
        self.assertEqual(land_apply.base_red(None, "scripts/test_x.py", sys.executable, root, {}, run=done(1)), (None, None))
        self.assertEqual(land_apply.base_red(root, "scripts/test_new.py", sys.executable, root, {}, run=done(1)), (False, None))
        with unittest.mock.patch.object(land_apply.grade_build, "sandboxed", side_effect=land_apply.grade_build.SandboxRefused("x")):
            self.assertEqual(land_apply.base_red(root, "scripts/test_x.py", sys.executable, root, {}, run=done(1)), (None, None))


if __name__ == "__main__":
    unittest.main()
