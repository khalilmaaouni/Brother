#!/usr/bin/env python3
"""S22 (1.1.6, advisory S22-T02): the grader refuses a build whose run rewrote a PRE EXISTING ignored file it did not
declare. git status never shows an ignored file as modified, so outside_scratch (new status lines only) let such a
rewrite through; the grader now hashes every ignored file in the sandbox before the code legs and compares after.
The entry point cases grade a fixture build through grade_build.main(), the call the loop makes, inside a throwaway
git repository under a temp folder (HOME, the slot locks and the slot sandboxes too).
Run: python3 -B scripts/loop/test_grade_build_ignored_overwrite.py"""
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
CONSTANTS = "A = 1\nB = 2\nC = 3\n\n"
OWN = ("import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport pool\n\n"
       "class Constants(unittest.TestCase):\n"
       "    def test_a(self):\n        self.assertEqual(pool.__dict__.get('A'), 1)\n"
       "    def test_b(self):\n        self.assertEqual(pool.__dict__.get('B'), 2)\n"
       "    def test_c(self):\n        self.assertEqual(pool.__dict__.get('C'), 3)\n"
       "%s\nif __name__ == '__main__':\n    unittest.main()\n")
REWRITE = ("    def test_rewrites_the_cache(self):\n"
           "        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache.log'), 'w') as fh:\n"
           "            fh.write('rewritten by the build')\n")
PLANTED = "scripts/cache.log"   # ignored by the fixture's .gitignore, present in the sandbox before the code legs


def _build(rewrite):
    return {"edits": [{"path": "scripts/pool.py", "find": POOL, "replace": CONSTANTS + POOL}],
            "tests": [{"path": "scripts/test_pool_s22.py", "new_file_content": OWN % (REWRITE if rewrite else "")}],
            "done_check": "python3 -B scripts/test_pool_s22.py",
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


def _git(cwd, *argv):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git"] + list(argv), cwd=cwd, env=env, check=True, capture_output=True)


class GradeAtTheEntryPoint(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="s22-grade-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "scripts"))
        for rel, text in (("scripts/pool.py", POOL), (".gitignore", "*.log\n")):
            with open(os.path.join(self.repo, rel), "w") as fh:
                fh.write(text)
        saved_env = {k: os.environ.get(k) for k in ("HOME", "BROTHER_LOAD_CAP", "BROTHER_SANDBOX")}
        os.environ["HOME"] = os.path.join(self.tmp, "home")
        os.environ.pop("BROTHER_LOAD_CAP", None)
        os.environ.pop("BROTHER_SANDBOX", None)
        self.addCleanup(self._restore_env, saved_env)
        # A SANDBOX INSIDE A SANDBOX (close_unit's landing box, the hermetic push check): sandbox-exec cannot apply a
        # profile there, so grade main() answers NO-DATA and none of these cases would grade. The scan under test runs in
        # the grader's own process, never in the confined child, so there the children run unconfined inside the outer
        # box, and the build carries no dynamic access so the strict screen that opting out restores admits it.
        if G.sandbox_ready():
            os.environ["BROTHER_SANDBOX"] = "off"
        _git(self.repo, "init", "-q"); _git(self.repo, "add", "-A")
        _git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        saved = (G.SANDBOXES, G.OLD_PYTHON, G._PREDICTION_LEDGER, G.scratch, G.ignored_hashes if hasattr(G, "ignored_hashes") else None,
                 list(sys.argv), os.getcwd())
        self.addCleanup(self._restore, saved)
        G.SANDBOXES = os.path.join(self.tmp, "sandboxes")
        # an in memory ledger: None made every grade write a "claim was not recorded" NO-DATA line, which close_unit
        # quotes first, so a green closure read as NO-DATA
        self.claims = []
        G._PREDICTION_LEDGER = type("Ledger", (), {"predict": staticmethod(lambda **kw: self.claims.append(kw))})
        G.PERSISTENT.clear(); del G.DEGRADED[:]
        other = _other_python()
        self.assertIsNotNone(other, "no second interpreter here: the grader's two version leg cannot run")
        G.OLD_PYTHON = other
        os.chdir(self.repo)
        # THE PRE EXISTING IGNORED FILE: the code leg's sandbox (tag r1) carries it before any command runs there, the
        # way a sandbox can hold any ignored file a scratch copy or an earlier step left; the build never declares it.
        real = G.scratch

        def planted(tag="r"):
            root = real(tag)
            if tag == "r1":
                with open(os.path.join(root, PLANTED), "w") as fh:
                    fh.write("present before the build")
            return root
        G.scratch = planted

    @staticmethod
    def _restore_env(saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _restore(saved):
        G.SANDBOXES, G.OLD_PYTHON, G._PREDICTION_LEDGER, G.scratch, hashes, argv, cwd = saved
        if hashes is not None:
            G.ignored_hashes = hashes
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

    def test_an_overwritten_ignored_file_refuses(self):
        code, out = self.grade(_build(rewrite=True))
        self.assertEqual(code, 1, out)
        self.assertIn("rewrote a pre existing ignored file", out)
        self.assertIn(PLANTED, out)

    def test_an_untouched_ignored_file_passes(self):
        code, out = self.grade(_build(rewrite=False))
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip().splitlines()[-1], "PASS", out)

    def test_an_unreadable_tree_refuses(self):
        real = G.ignored_hashes
        calls = []

        def second_unreadable(root, runner=None):
            calls.append(root)
            return real(root) if len(calls) == 1 else None
        G.ignored_hashes = second_unreadable
        code, out = self.grade(_build(rewrite=False))
        self.assertEqual(code, 3, out)   # NO-DATA: the build was not judged, never a PASS
        self.assertIn("ignored files were not checked", out)
        self.assertNotEqual(out.strip().splitlines()[-1], "PASS", out)
        self.assertEqual(len(calls), 2, "the grader hashes once before and once after the code legs")


class InsideAnotherSandbox(GradeAtTheEntryPoint):
    """The same three entry point cases on a host where sandbox-exec cannot apply a profile: close_unit's landing box and
    the hermetic push check (exit 71, "sandbox_apply: Operation not permitted"). Forced here through the grader's own probe
    cache, so a bare run proves what a boxed run meets."""
    def setUp(self):
        saved = list(G._APPLIES)
        self.addCleanup(G._APPLIES.__setitem__, slice(None), saved)
        G._APPLIES[:] = ["sandbox-exec cannot apply a profile here (exit 71: forced): a sandbox inside a sandbox"]
        super().setUp()

    def test_the_grade_ran_unconfined_and_said_so(self):
        code, out = self.grade(_build(rewrite=False))
        self.assertEqual(code, 0, out)
        self.assertIn("SANDBOX OFF: BROTHER_SANDBOX=off, running unsandboxed", out)


class TheTwoFunctions(unittest.TestCase):
    def test_a_declared_file_may_change(self):
        before = {"a.log": "1", "b.log": "1", "c.log": "1"}
        after = {"a.log": "2", "b.log": "2", "c.log": "1"}
        self.assertEqual(G.ignored_overwrites(before, after, {"a.log"}), ["b.log"])
        self.assertEqual(G.ignored_overwrites(before, after, set()), ["a.log", "b.log"])
        self.assertEqual(G.ignored_overwrites(before, after, {"a.log", "b.log"}), [])

    def test_an_unreadable_side_refuses(self):
        for before, after in ((None, {}), ({}, None), (None, None), ({"a": "1"}, ["a"])):
            self.assertEqual(G.ignored_overwrites(before, after, set()), [G.IGNORED_UNREADABLE], (before, after))
        # a file that could not be read on one side is never read as unchanged
        self.assertEqual(G.ignored_overwrites({"a": G.UNREADABLE_FILE}, {"a": G.UNREADABLE_FILE}, set()), ["a"])

    def test_created_deleted_and_unchanged_are_not_this_scan(self):
        self.assertEqual(G.ignored_overwrites({"gone": "1", "same": "1"}, {"same": "1", "new": "1"}, set()), [])
        self.assertEqual(G.ignored_overwrites({}, {}, set()), [])

    def test_an_unreadable_tree_refuses(self):
        fail = lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", b"fatal: not a git repository")
        self.assertIsNone(G.ignored_hashes("/nonexistent", runner=fail))
        self.assertIsNone(G.ignored_hashes("/nonexistent/s22-no-such-dir"))   # the real git, a root it cannot read

    def test_hostile_ignored_files_are_hashed_without_following(self):
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="s22-hash-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, ".gitignore"), "w") as fh:
            fh.write("*.log\ncache/\n.grade-sandbox/\n")
        _git(tmp, "init", "-q"); _git(tmp, "add", ".gitignore")
        _git(tmp, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "b")
        os.makedirs(os.path.join(tmp, "cache", "sub")); os.makedirs(os.path.join(tmp, ".grade-sandbox"))
        for rel, text in (("cache/sub/x.pyc", "a"), ("we ird\nname.log", "b"), (".grade-sandbox/home.log", "c")):
            with open(os.path.join(tmp, rel), "w") as fh:
                fh.write(text)
        os.symlink("/etc", os.path.join(tmp, "etc.log"))
        got = G.ignored_hashes(tmp)
        self.assertEqual(sorted(got), ["cache/sub/x.pyc", "etc.log", "we ird\nname.log"], got)   # the scratch root is exempt
        self.assertEqual(got["etc.log"], "symlink:/etc")   # recorded by its target text, never followed
        self.assertEqual(len(got["cache/sub/x.pyc"]), 64)
        with open(os.path.join(tmp, "cache", "sub", "x.pyc"), "w") as fh:
            fh.write("")   # truncated by the build: a new hash
        self.assertEqual(G.ignored_overwrites(got, G.ignored_hashes(tmp), set()), ["cache/sub/x.pyc"])


if __name__ == "__main__":
    unittest.main()
