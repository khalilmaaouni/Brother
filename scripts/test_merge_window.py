#!/usr/bin/env python3
"""scripts/merge_window.sh, run for real at its entry point over a throwaway repository (unit A2.e).

THE FIXTURE: a git repository holding scripts/close_unit.py's whole import closure (kernel_closure.py --roots, the
derivation the preflight uses), the sandbox profile it boxes done checks with, merge_window.sh itself and a two unit
plan; two landed branches add one done check each, the second with one skipped test. A plan generator named by
MW_PLAN_GEN writes a file, so the regeneration commit is observed too.

WHAT IT PROVES: the clean unit reads DONE on both interpreters and the skipping unit PARTIAL (A1: a skip is not a
pass), so the window ends FAIL and exits 1; with the skipping branch left out the window ends PASS and exits 0; the
refusals before any merge (dirty tree, unknown unit, unresolvable ref, a unit named twice) exit 2 and merge nothing; a
conflicting branch is aborted with the earlier merges named. INSIDE A SANDBOX (this file run boxed, where a nested
sandbox is refused) close_unit answers NO-DATA, and the window must then say NOT DONE, never DONE: that branch is
asserted instead, so no case is ever skipped. Standard library only; the fixture lives in a temp directory with its
own HOME and is removed afterwards."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
import grade_build as G  # noqa: E402  (sandbox_ready: whether a sandbox may run here, the one probe)

NESTED = bool(G.sandbox_ready())
PLAN = "docs/plan/MW-FIXTURE-WBS.json"
GOOD = "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n\n" \
       "if __name__ == '__main__':\n    unittest.main()\n"
SKIPS = GOOD.replace("    def test_ok(self):", "    @unittest.skip('not yet')\n    def test_later(self):\n        pass\n\n"
                     "    def test_ok(self):")
UNITS = """{"units": [
 {"id": "MA", "state": "OPEN", "sub_units": ["MA.a"], "evidence": "MA.a landed", "done_check": "python3 -B scripts/test_mw_a.py"},
 {"id": "MB", "state": "OPEN", "sub_units": ["MB.a"], "evidence": "MB.a landed", "done_check": "python3 -B scripts/test_mw_b.py"}
]}
"""
GEN = "with open('GENERATED.txt', 'w') as fh:\n    fh.write('regenerated\\n')\n"


def closure():
    r = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "kernel_closure.py"), "--roots",
                        "scripts/close_unit.py"], cwd=REPO, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("close_unit's closure could not be derived: " + r.stdout + r.stderr)
    return r.stdout.split() + ["scripts/loop/sandbox.sb", "scripts/merge_window.sh"]


class MergeWindow(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.mkdtemp(prefix="merge-window-")
        self.addCleanup(shutil.rmtree, self.box, True)
        self.root, home = os.path.join(self.box, "repo"), os.path.join(self.box, "home")
        os.makedirs(os.path.join(home, "tmp"))
        for rel in closure():
            if os.path.isfile(os.path.join(REPO, rel)):
                os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
                shutil.copy2(os.path.join(REPO, rel), os.path.join(self.root, rel))
        self.write({PLAN: UNITS, "gen_fixture.py": GEN})
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER", "MW_"))}
        self.env.update(HOME=home, TMPDIR=os.path.join(home, "tmp"), BROTHER_WBS=PLAN, MW_PLAN_GEN="gen_fixture.py",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.commit("base")
        for branch, name, body in (("a", "scripts/test_mw_a.py", GOOD), ("b", "scripts/test_mw_b.py", SKIPS),
                                   ("clash", "scripts/test_mw_a.py", GOOD.replace("True", "1"))):
            self.git("checkout", "-q", "-b", branch, "main")
            self.write({name: body})
            self.commit(branch)
        self.git("checkout", "-q", "main")
        self.base = self.git("rev-parse", "HEAD").strip()

    def write(self, files):
        for rel, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)) or self.root, exist_ok=True)
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write(body)

    def git(self, *argv):
        return subprocess.run(["git"] + list(argv), cwd=self.root, env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def commit(self, why):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", why)

    def window(self, *pairs):
        r = subprocess.run(["sh", "scripts/merge_window.sh"] + list(pairs), cwd=self.root, env=self.env,
                           capture_output=True, text=True, timeout=900)
        return r.returncode, r.stdout + r.stderr

    def line(self, out, unit):
        got = [l for l in out.splitlines() if l.startswith(unit + " merged ")]
        self.assertEqual(len(got), 1, out)
        return got[0]

    def test_a_clean_unit_closes_and_a_skipping_one_is_partial(self):
        code, out = self.window("MA=a", "MB=b")
        self.assertEqual(code, 1, out)
        a, b = self.line(out, "MA"), self.line(out, "MB")
        self.assertRegex(a, r"^MA merged [0-9a-f]{12} py3=0 py3\.9=0 ")
        self.assertIn("REGENERATE python3 gen_fixture.py", out)
        self.assertIn("regenerated after the merges", self.git("log", "--format=%s"))
        if NESTED:   # close_unit cannot box inside a box: NO-DATA, and the window never says DONE
            self.assertRegex(a, r" NOT DONE: .*NO-DATA")
            self.assertRegex(b, r" NOT DONE: .*NO-DATA")
        else:
            self.assertTrue(a.endswith(" DONE"), a)
            self.assertRegex(b, r" NOT DONE: .*PARTIAL: 1 test\(s\) skipped")
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("FAIL: merge window"), out)

    def test_the_pass_line_needs_every_unit_done(self):
        code, out = self.window("MA=a")
        last = out.rstrip().splitlines()[-1]
        if NESTED:
            self.assertEqual(code, 1, out)
            self.assertTrue(last.startswith("FAIL: "), out)
        else:
            self.assertEqual(code, 0, out)
            self.assertTrue(re.match(r"PASS: merge window, MA DONE at [0-9a-f]{12}; start the gate", last), out)
            self.assertIn('"state": "DONE"', self.git("show", "HEAD:" + PLAN))

    def test_a_red_or_missing_second_interpreter_stops_the_pass_line(self):
        """Orthogonal to the closer: close_unit runs python3 and says DONE; only the 3.9 column is red or absent."""
        for py39, want in (("/usr/bin/false", "py3.9=1"), ("/no/such/python", "py3.9=NO-DATA(/no/such/python missing)")):
            self.git("reset", "-q", "--hard", self.base)   # the fixture's own branch, back to its base commit
            self.env["MW_PY39"] = py39
            code, out = self.window("MA=a")
            self.assertEqual(code, 1, out)
            self.assertIn(want, self.line(out, "MA"))
            self.assertTrue(out.rstrip().splitlines()[-1].startswith("FAIL: merge window"), out)

    def test_refusals_before_any_merge_merge_nothing(self):
        head = self.git("rev-parse", "HEAD")
        for pairs, why in ((["MZ=a"], "not a unit of"), (["MA=nowhere"], "cannot resolve"),
                           (["MA=a", "MA=b"], "named twice"), (["MA"], "is not UNIT=REF"), ([], "at least one")):
            code, out = self.window(*pairs)
            self.assertEqual((code, why in out), (2, True), (pairs, out))
        self.write({"stray.txt": "x"})
        code, out = self.window("MA=a")
        self.assertEqual((code, "not clean" in out), (2, True), out)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_a_red_generator_stops_the_window_before_any_close(self):
        self.write({"gen_fixture.py": "raise SystemExit(3)\n"})
        self.commit("a red generator")
        code, out = self.window("MA=a")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL: the generator 'python3 gen_fixture.py' exited 3", out)
        self.assertNotIn("MA merged", out)
        self.assertIn('"state": "OPEN"', self.git("show", "HEAD:" + PLAN))

    def test_a_conflict_aborts_and_names_the_merges_before_it(self):
        code, out = self.window("MA=a", "MB=clash")
        self.assertEqual(code, 2, out)
        self.assertIn("MB MERGE-CONFLICT clash: aborted; merged before it: MA", out)
        self.assertEqual(self.git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
