#!/usr/bin/env python3
"""test_loop_tool_parity.drifted: an executed copy that equals the versioned copy at the revision its
deploy stamp declares is NOT a drift; one that equals neither this tree nor that revision still is.

Fixture, never this machine's ~/.claude/bin: a throwaway repository whose scripts/loop/x.py is
committed twice (A, then B at HEAD), and a bin directory holding the A copy plus a .deploy-stamp.json
naming A, exactly what scripts/loop/deploy_stamped.py writes. Four cases, one condition each:

  1. bin equals A, stamp names A          -> PASS (this is the merge gate case of 2026-09-30)
  2. bin equals A, no stamp                -> FAIL drift (the pre stamp behaviour is unchanged)
  3. bin is a hand edit, stamp names A     -> FAIL drift (the stamp never excuses a copy it did not produce)
  4. bin equals A, stamp names a stranger  -> FAIL drift (a revision this repository cannot show is no alibi)

Mutation guard: revert the stamped_revision branch in drifted() and case 1 goes red.

Run: python3 scripts/test_loop_tool_parity_stamp.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class TestStampAwareDrift(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.mkdtemp(prefix="parity-stamp-")
        self.source = os.path.join(self.box, "source")
        self.loop = os.path.join(self.source, "scripts", "loop")
        self.bin = os.path.join(self.box, "bin")
        os.makedirs(self.loop)
        os.makedirs(self.bin)
        for name in ("test_loop_tool_parity.py", "install_loop_tools.py"):
            shutil.copy2(os.path.join(HERE, name), os.path.join(self.source, "scripts", name))
        self.env = dict(os.environ, BROTHER_DEPLOY_TARGET=self.bin, HOME=self.box,
                        PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_GLOBAL="/dev/null",
                        GIT_CONFIG_NOSYSTEM="1")
        self.tool = os.path.join(self.loop, "x.py")
        self.write(self.tool, "print('A')\n")
        self.git("init", "--quiet")
        self.git("add", ".")
        self.commit("A")
        self.revision_a = self.git("rev-parse", "HEAD").stdout.strip()
        self.write(self.tool, "print('B')\n")
        self.git("add", ".")
        self.commit("B")
        self.write(os.path.join(self.bin, "x.py"), "print('A')\n")

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o644)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.source] + list(args), env=self.env, check=True,
                              capture_output=True, text=True)

    def commit(self, message):
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                 "commit", "--quiet", "-m", message)

    def stamp(self, revision):
        with open(os.path.join(self.bin, ".deploy-stamp.json"), "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "revision": revision, "source_dirty": False}, handle)

    def parity(self):
        return subprocess.run([sys.executable, "-B", os.path.join(self.source, "scripts", "test_loop_tool_parity.py")],
                              env=self.env, cwd=self.source, capture_output=True, text=True, timeout=120)

    def test_1_copy_at_its_stamped_revision_is_not_a_drift(self):
        self.stamp(self.revision_a)
        result = self.parity()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note revision  x.py", result.stdout)
        self.assertIn(self.revision_a[:12], result.stdout)
        self.assertNotIn("FAIL drift", result.stdout)

    def test_2_unstamped_copy_behind_this_tree_is_still_a_drift(self):
        result = self.parity()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL drift    x.py", result.stdout)

    def test_3_hand_edited_copy_is_a_drift_whatever_the_stamp_says(self):
        self.stamp(self.revision_a)
        self.write(os.path.join(self.bin, "x.py"), "print('hand edit')\n")
        result = self.parity()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL drift    x.py", result.stdout)

    def test_4_a_revision_this_repository_cannot_show_is_no_alibi(self):
        self.stamp("f" * 40)
        result = self.parity()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL drift    x.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
