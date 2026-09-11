#!/usr/bin/env python3
"""reclaim_worktrees.py's own checks, driven backwards as well as forwards.

in_use() is the fail-closed safety property the docstring narrates the
2026-09-10 incident against (two worktrees removed while a process still ran
inside them). Nothing drove it backwards before this file: an lsof that
errors, times out, or exits anything other than 0/1 must read as IN USE, and
the three HELD conditions (uncommitted, unpushed, the invoking checkout
itself) and the RELEASED path (through `git worktree remove`, never rm -rf)
were asserted only by reading the source, never by running it.

Mirrors the shape of scripts/test_cutover_pack.py: sys.path insert, import
the module under test directly, unittest.TestCase per property, verbosity=2.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reclaim_worktrees as RW  # noqa: E402


def run_git(args, cwd=None):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                   capture_output=True, text=True)


class InUseFailsClosed(unittest.TestCase):
    """Case (a)+(b)+(c): drive in_use()'s lsof call backwards without a
    real process or a real lsof binary."""

    def test_oserror_from_subprocess_reads_as_in_use(self):
        with mock.patch.object(RW.subprocess, "run",
                               side_effect=OSError("lsof not found")):
            self.assertTrue(RW.in_use("/tmp/does-not-matter"))

    def test_exit_2_reads_as_in_use(self):
        fake = mock.Mock(returncode=2, stdout="")
        with mock.patch.object(RW.subprocess, "run", return_value=fake):
            self.assertTrue(RW.in_use("/tmp/does-not-matter"))

    def test_exit_1_with_empty_output_reads_as_free(self):
        fake = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(RW.subprocess, "run", return_value=fake):
            self.assertFalse(RW.in_use("/tmp/does-not-matter"))

    def test_exit_0_with_a_pid_line_reads_as_in_use(self):
        fake = mock.Mock(returncode=0, stdout="p12345\ncwd  /tmp/does-not-matter\n")
        with mock.patch.object(RW.subprocess, "run", return_value=fake):
            self.assertTrue(RW.in_use("/tmp/does-not-matter"))


class RealGitFixture(unittest.TestCase):
    """Cases (d), (e), (f), (g) need real git state: `git status
    --porcelain`, `branch -r --contains HEAD`, and `worktree remove` are not
    worth mocking without reimplementing them, and mocking git here would
    only prove the mock is self-consistent, not that the script is.

    Paths are compared through os.path.realpath because the script itself
    realpaths every root and worktree before printing it (macOS resolves
    /var to /private/var, and a raw tempfile.mkdtemp() path will not match
    the printed line otherwise)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reclaim-worktrees-test-")
        self.remote = os.path.join(self.tmp, "remote.git")
        run_git(["init", "--bare", "-q", self.remote])
        self.root = os.path.join(self.tmp, "root")
        run_git(["clone", "-q", self.remote, self.root])
        self.real_root = os.path.realpath(self.root)
        run_git(["-C", self.root, "config", "user.email", "t@example.com"])
        run_git(["-C", self.root, "config", "user.name", "t"])
        with open(os.path.join(self.root, "a.txt"), "w") as f:
            f.write("root\n")
        run_git(["-C", self.root, "add", "a.txt"])
        run_git(["-C", self.root, "commit", "-q", "-m", "init"])
        self.base_branch = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        run_git(["-C", self.root, "push", "-q", "-u", "origin", self.base_branch])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _worktree(self, name, branch, pushed, dirty=False):
        path = os.path.join(self.tmp, name)
        run_git(["-C", self.root, "worktree", "add", "-q", "-b", branch, path])
        run_git(["-C", path, "config", "user.email", "t@example.com"])
        run_git(["-C", path, "config", "user.name", "t"])
        with open(os.path.join(path, "b.txt"), "w") as f:
            f.write(name + "\n")
        run_git(["-C", path, "add", "b.txt"])
        run_git(["-C", path, "commit", "-q", "-m", "wt commit"])
        if pushed:
            run_git(["-C", path, "push", "-q", "-u", "origin", branch])
        if dirty:
            with open(os.path.join(path, "c.txt"), "w") as f:
                f.write("uncommitted\n")
        return os.path.realpath(path)

    def _run_main(self, extra_args):
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(sys, "argv",
                               ["reclaim_worktrees.py", "--roots", self.root] + extra_args):
            with contextlib.redirect_stdout(buf):
                rc = RW.main()
        return rc, buf.getvalue()

    def test_uncommitted_worktree_is_held(self):
        path = self._worktree("wt-dirty", "wt-dirty-branch", pushed=True, dirty=True)
        _, out = self._run_main([])
        self.assertIn(f"HELD    uncommitted   {path}", out, out)

    def test_unpushed_head_is_held(self):
        path = self._worktree("wt-unpushed", "wt-unpushed-branch", pushed=False)
        _, out = self._run_main([])
        self.assertIn(f"HELD    unpushed HEAD {path}", out, out)

    def test_invoking_checkout_is_never_a_candidate(self):
        self._worktree("wt-other", "wt-other-branch", pushed=True)
        _, out = self._run_main([])
        for line in out.splitlines():
            fields = line.split()
            if fields:
                self.assertNotEqual(fields[-1], self.real_root, out)

    def test_clean_and_pushed_worktree_would_release(self):
        path = self._worktree("wt-clean", "wt-clean-branch", pushed=True)
        _, out = self._run_main([])
        self.assertIn(f"WOULD RELEASE          {path}", out, out)

    def test_apply_removes_through_git_worktree_remove_not_rm_rf(self):
        path = self._worktree("wt-release-me", "wt-release-me-branch", pushed=True)
        calls = []
        real_run = subprocess.run

        def spy(args, *a, **kw):
            calls.append(list(args))
            return real_run(args, *a, **kw)

        with mock.patch.object(RW.subprocess, "run", side_effect=spy):
            rc, out = self._run_main(["--apply"])
        self.assertEqual(rc, 0, out)
        self.assertIn(f"RELEASED               {path}", out, out)
        self.assertIn(["git", "-C", self.real_root, "worktree", "remove", path], calls)
        # the directory is gone through git's own bookkeeping, not a stray
        # rm -rf: a hand-deleted directory leaves a stale registration that
        # makes the next `worktree add` fail into the main checkout.
        self.assertFalse(os.path.isdir(path))
        listing = subprocess.run(
            ["git", "-C", self.root, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True).stdout
        self.assertNotIn(path, listing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
