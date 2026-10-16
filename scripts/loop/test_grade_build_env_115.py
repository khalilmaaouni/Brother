#!/usr/bin/env python3
"""C21 (register rows L17, L16, 2026-10-14): the grader runs neighbour suites under a fake HIGH load and refuses a run
that wrote outside the scratch root. Every case grades a fixture build through grade_build.main(), the entry point the
loop runs, inside a throwaway git repository under a temp folder (HOME, the slot locks and the slot sandboxes too).
Run: python3 -B scripts/loop/test_grade_build_env_115.py"""
import contextlib
import io
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

POOL = "def admit():\n    return True\n"
NEIGHBOUR = ("import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport pool\n\n"
             "class Admits(unittest.TestCase):\n    def test_admits(self):\n        self.assertTrue(pool.admit())\n\n"
             "if __name__ == '__main__':\n    unittest.main()\n")
CONSTANTS = "A = 1\nB = 2\nC = 3\n\n"
QUIET_ONLY = ("import os\n" + CONSTANTS + "def admit():\n    cap = os.environ.get('BROTHER_LOAD_CAP')\n"
              "    return not cap or float(cap) < (os.cpu_count() or 1)\n")
OWN = ("import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport pool\n\n"
       "class Constants(unittest.TestCase):\n"
       "    def test_a(self):\n        self.assertEqual(getattr(pool, 'A', None), 1)\n"
       "    def test_b(self):\n        self.assertEqual(getattr(pool, 'B', None), 2)\n"
       "    def test_c(self):\n        self.assertEqual(getattr(pool, 'C', None), 3)\n"
       "    def test_own_command_is_not_pinned(self):\n        self.assertNotIn('BROTHER_LOAD_CAP', os.environ)\n"
       "%s\nif __name__ == '__main__':\n    unittest.main()\n")
LITTER = ("    def test_writes_into_the_tree(self):\n        os.makedirs('x', exist_ok=True)\n"
          "        with open(os.path.join('x', 'out.txt'), 'w') as fh:\n            fh.write('1')\n"
          "        with open('cache.log', 'w') as fh:\n            fh.write('1')\n")


def _build(pool_text, litter=False, base=POOL):
    return {"edits": [{"path": "scripts/pool.py", "find": base, "replace": pool_text}],
            "tests": [{"path": "scripts/test_pool_c21.py", "new_file_content": OWN % (LITTER if litter else "")}],
            "done_check": "python3 -B scripts/test_pool_c21.py",
            "mutations": [{"name": "M-A", "path": "scripts/pool.py", "find": "A = 1", "replace": "A = 7", "caught_by": "test_a"},
                          {"name": "M-B", "path": "scripts/pool.py", "find": "B = 2", "replace": "B = 7", "caught_by": "test_b"},
                          {"name": "M-C", "path": "scripts/pool.py", "find": "C = 3", "replace": "C = 7", "caught_by": "test_c"}]}


def _other_python():
    """An interpreter of another minor version than this one, so the grader's two version leg runs; None when none."""
    mine = "%d.%d" % sys.version_info[:2]
    for cand in ("/usr/bin/python3", shutil.which("python3") or "", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if cand and os.path.exists(cand) and G.interpreter_version(cand) not in (None, mine):
            return cand
    return None


class GradeAtTheEntryPoint(unittest.TestCase):
    BASE_POOL = POOL

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="c21-grade-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "scripts"))
        for rel, text in (("scripts/pool.py", self.BASE_POOL), ("scripts/test_pool.py", NEIGHBOUR), (".gitignore", "*.log\n")):
            with open(os.path.join(self.repo, rel), "w") as fh:
                fh.write(text)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["HOME"] = os.path.join(self.tmp, "home")
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"]):
            subprocess.run(argv, cwd=self.repo, env=env, check=True, capture_output=True)
        saved_env = {k: os.environ.get(k) for k in ("HOME", "BROTHER_LOAD_CAP", "BROTHER_SANDBOX")}
        os.environ["HOME"] = env["HOME"]
        os.environ.pop("BROTHER_LOAD_CAP", None)     # the quiet machine: no load is read unless the grader pins one
        os.environ.pop("BROTHER_SANDBOX", None)
        self.addCleanup(self._restore_env, saved_env)
        saved = (G.SANDBOXES, G.OLD_PYTHON, G._PREDICTION_LEDGER, list(sys.argv), os.getcwd())
        self.addCleanup(self._restore, saved)
        G.SANDBOXES = os.path.join(self.tmp, "sandboxes")
        G._PREDICTION_LEDGER = None
        G.PERSISTENT.clear(); del G.DEGRADED[:]
        other = _other_python()
        self.assertIsNotNone(other, "no second interpreter here: the grader's two version leg cannot run")
        G.OLD_PYTHON = other
        os.chdir(self.repo)

    @staticmethod
    def _restore_env(saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _restore(saved):
        G.SANDBOXES, G.OLD_PYTHON, G._PREDICTION_LEDGER, argv, cwd = saved
        sys.argv[:] = argv
        os.chdir(cwd)
        G.PERSISTENT.clear(); del G.DEGRADED[:]

    def grade(self, build):
        path = os.path.join(self.tmp, "build.json")
        with open(path, "w") as fh:
            json.dump(build, fh)
        sys.argv = ["grade_build.py", path]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.main()
        return code, out.getvalue()

    def test_a_clean_build_passes(self):
        code, out = self.grade(_build(CONSTANTS + POOL))
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "PASS", out)
        self.assertNotIn("SCRATCH", out)

    def test_a_build_that_passes_only_on_a_quiet_machine_is_refused_with_its_cause(self):
        code, out = self.grade(_build(QUIET_ONLY))
        self.assertEqual(code, 1, out)
        self.assertIn("neighbour suite python3 -B scripts/test_pool.py depends on the host load", out)
        self.assertIn("BROTHER_LOAD_CAP=1000", out)

    def test_a_build_whose_test_writes_into_the_tree_is_refused_with_the_path(self):
        code, out = self.grade(_build(CONSTANTS + POOL, litter=True))
        self.assertEqual(code, 1, out)
        self.assertIn("wrote outside the scratch root .grade-sandbox/", out)
        self.assertIn("x/out.txt", out)
        self.assertIn("cache.log", out, "an ignored path written into the tree is still a write outside the scratch root")


class ANeighbourAlreadyLoadBoundAtBase(GradeAtTheEntryPoint):
    """The base itself only passes on a quiet machine: under the same pin the base run is red, so the neighbour is
    dropped and the build is not blamed for what it did not write."""
    BASE_POOL = QUIET_ONLY.replace(CONSTANTS, "")

    def test_a_clean_build_passes(self):
        code, out = self.grade(_build(CONSTANTS + self.BASE_POOL.replace("import os\n", ""), base=self.BASE_POOL.replace("import os\n", "")))
        self.assertEqual(code, 0, out)
        self.assertIn("red at base (under BROTHER_LOAD_CAP=1000), dropped: python3 -B scripts/test_pool.py", out)

    test_a_build_that_passes_only_on_a_quiet_machine_is_refused_with_its_cause = None
    test_a_build_whose_test_writes_into_the_tree_is_refused_with_the_path = None


class TheScanFailsClosed(unittest.TestCase):
    def test_a_git_location_variable_never_redirects_the_scan(self):
        tmp = tempfile.mkdtemp(prefix="c21-scan-")
        self.addCleanup(shutil.rmtree, tmp, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        subprocess.run(["git", "init", "-q"], cwd=tmp, env=env, check=True, capture_output=True)
        old = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = os.path.join(tmp, "no-such-git-dir")
        try:
            self.assertEqual(G.tree_writes(tmp), set())
        finally:
            if old is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = old

    def test_an_unreadable_tree_is_never_clean(self):
        self.assertEqual(G.outside_scratch(None, set()), ["<tree state unreadable>"])
        self.assertEqual(G.outside_scratch(set(), None), ["<tree state unreadable>"])
        self.assertEqual(G.outside_scratch(set(), ["?? x"]), ["<tree state unreadable>"])

    def test_the_scratch_root_is_exempt_and_everything_else_is_named(self):
        before = {" M scripts/pool.py"}
        now = before | {"?? .grade-sandbox/tmp/a", "?? .Grade-Sandbox/b", "?? x/out.txt", " D scripts/gone.py",
                        "!! build/cache.bin"}
        self.assertEqual(G.outside_scratch(before, now), ["build/cache.bin", "scripts/gone.py", "x/out.txt"])
        self.assertEqual(G.outside_scratch(before, set(before)), [])


if __name__ == "__main__":
    unittest.main()
