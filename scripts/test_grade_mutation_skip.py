#!/usr/bin/env python3
"""A build whose suite is NOT green with its code pays for no mutation: its verdict is already FAIL.

WHY. 2026-10-05: the MG1.e and PR1.c grades of the proof supply hit "FAIL stage timed out after 1800s" at load 2 to 3.
Measured alone the same day, the PR1.c grade took 2186 s: GREEN-WITH-CODE failed at +493 s, then six mutations ran
anyway at about 280 s each, every one "caught", because a mutation of a tree that is already red is red whatever the
tests check. The verdict line read "FAIL: suite not green with the code" either way: the mutations only count toward
a verdict when the run is green (the mutation reason is gated on `red and green == 0`). They were pure cost.

HOW. Through the grader's entry point, run as a child in a fixture git repository it owns, with its own HOME (so its
machine slot and sandboxes are this test's): one build that is red without its code and still red with it (the skip
fires, no mutation line, FAIL names the suite), and one healthy build (the mutation suite still runs and catches all
three), so the saving can never become a hole.

Run: python3 -B scripts/test_grade_mutation_skip.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

GRADER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "grade_build.py")
BASE = "def f():\n    return 0\n"
MUTATIONS = [{"name": "returns five", "path": "scripts/m.py", "find": "return 1", "replace": "return 5"},
             {"name": "returns none", "path": "scripts/m.py", "find": "return 1", "replace": "return None"},
             {"name": "returns minus one", "path": "scripts/m.py", "find": "return 1", "replace": "return -1"}]


def test_file(expected):
    return ("import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport m\n\n\n"
            "class T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(m.f(), %d)\n\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n" % expected)


def _sandbox_refusal():
    """The grader's own answer to "may a sandbox run here" (grade_build.sandbox_ready), asked in a child so this module
    never imports the grader: '' when it may. Inside another sandbox (the pre-push hermetic gate) it cannot apply."""
    if not shutil.which("git"):
        return "no git on this host"
    r = subprocess.run([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import grade_build as G; "
                        "print(G.sandbox_ready())", os.path.dirname(GRADER)], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else "the grader's sandbox probe failed: %s" % (r.stderr.strip()[-200:],)


SANDBOX_REFUSAL = _sandbox_refusal()


@unittest.skipIf(SANDBOX_REFUSAL, "NO-DATA: the grader cannot sandbox here (%s)" % SANDBOX_REFUSAL)
class MutationsRunOnlyOnAGreenTree(unittest.TestCase):
    def setUp(self):
        self.box = os.path.realpath(tempfile.mkdtemp(prefix="grade-mut-skip-"))
        self.addCleanup(shutil.rmtree, self.box, True)
        self.repo = os.path.join(self.box, "repo")
        os.makedirs(os.path.join(self.repo, "scripts"))
        with open(os.path.join(self.repo, "scripts", "m.py"), "w") as fh:
            fh.write(BASE)
        env = self.env()
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "core.hooksPath=/dev/null",
                      "commit", "-q", "-m", "base"]):
            subprocess.run(["git"] + args, cwd=self.repo, env=env, check=True, capture_output=True)

    def env(self):
        home = os.path.join(self.box, "home")
        os.makedirs(home, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER_", "PYTHON"))}
        env.update(HOME=home, PYTHONDONTWRITEBYTECODE="1", LOCAL_SLOTS="1",
                   BROTHER_GRADE_SANDBOXES=os.path.join(self.box, "sandboxes"))
        return env

    def grade(self, expected):
        build = {"edits": [{"path": "scripts/m.py", "find": "return 0", "replace": "return 1"}],
                 "tests": [{"path": "scripts/test_m.py", "new_file_content": test_file(expected)}],
                 "done_check": "python3 -B scripts/test_m.py", "mutations": MUTATIONS}
        os.makedirs(os.path.join(self.box, "wave", "out"), exist_ok=True)
        path = os.path.join(self.box, "wave", "out", "x-r0-build.json")
        with open(path, "w") as fh:
            json.dump(build, fh)
        r = subprocess.run([sys.executable, "-B", GRADER, path], cwd=self.repo, env=self.env(),
                           capture_output=True, text=True, timeout=600)
        return r.returncode, r.stdout + r.stderr

    def test_a_suite_red_with_the_code_runs_no_mutation(self):
        code, out = self.grade(2)   # m.f() is 0 at base and 1 with the code: red both ways
        self.assertEqual(code, 1, out)
        self.assertIn("RED-WITHOUT-CODE   yes", out)
        self.assertIn("GREEN-WITH-CODE    NO", out)
        self.assertIn("MUTATIONS          SKIPPED, 3 not run", out)
        self.assertNotIn("   mutation ", out)
        self.assertIn("FAIL: suite not green with the code", out)

    def test_a_green_build_still_pays_for_its_whole_mutation_suite(self):
        code, out = self.grade(1)
        self.assertIn("GREEN-WITH-CODE    yes", out)
        self.assertNotIn("SKIPPED", out)
        self.assertEqual(out.count("   mutation "), 3, out)
        self.assertIn("MUTATIONS          3 of 3 applied were caught", out)


if __name__ == "__main__":
    unittest.main()
