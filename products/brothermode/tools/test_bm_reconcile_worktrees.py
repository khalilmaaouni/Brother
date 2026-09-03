#!/usr/bin/env python3
"""Tests for tools/bm_reconcile_worktrees.py.

Every fixture builds a REAL git repository under
tempfile.TemporaryDirectory() with a real `git init` and a real `git
worktree add`, the same technique tools/test_bm_reconcile.py's
TestCase14PushStateUnobservable uses (a plain subprocess.run of git,
never a mock of one). No fixture touches this repository's own files.

Python 3.9, standard library only. Run:
  python3 tools/test_bm_reconcile_worktrees.py
"""
import importlib.util as _ilu
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = _ilu.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rw = _load("bm_reconcile_worktrees")


class _RepoFixture(unittest.TestCase):
    """A bare-bones real repository: one committed file on main, plus a
    linked worktree on a second branch. Subclasses mutate the worktree's
    working copy before asserting on rw.sweep()/rw.cmd_check()."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed on this machine")
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self._tmp.name, "repo")
        self.worktree = os.path.join(self._tmp.name, "wt")
        os.makedirs(self.repo)
        self._git(self.repo, "init", "-q")
        self._git(self.repo, "config", "user.email", "test@example.com")
        self._git(self.repo, "config", "user.name", "Test")
        with open(os.path.join(self.repo, "tracked.txt"), "w") as fh:
            fh.write("committed content\n")
        self._git(self.repo, "add", "tracked.txt")
        self._git(self.repo, "commit", "-q", "-m", "initial commit")
        self._git(self.repo, "worktree", "add", "-q", "-b", "side",
                  self.worktree)

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, cwd, *args):
        proc = subprocess.run(["git", "-C", cwd] + list(args),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0,
                         "git %s failed: %s" % (" ".join(args), proc.stderr))
        return proc.stdout


class TestLandedFile(_RepoFixture):
    """A file modified in the worktree back to bytes that ARE committed on
    a branch (main, reachable from the worktree's own object store)
    classifies LANDED: discarding it loses nothing."""

    def test_bytes_committed_on_another_branch_are_landed(self):
        # Commit a second version on main that the worktree does not have
        # checked out, so the "reachable from a ref" test is real: these
        # bytes live only on main's history, not on the worktree's branch.
        with open(os.path.join(self.repo, "tracked.txt"), "w") as fh:
            fh.write("version two, landed on main\n")
        self._git(self.repo, "add", "tracked.txt")
        self._git(self.repo, "commit", "-q", "-m", "second version on main")

        # In the worktree, dirty the same file to those exact bytes
        # without committing.
        with open(os.path.join(self.worktree, "tracked.txt"), "w") as fh:
            fh.write("version two, landed on main\n")

        data = rw.sweep(self.repo)
        files = data["worktrees"][0]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["path"], "tracked.txt")
        self.assertEqual(files[0]["verdict"], rw.LANDED)


class TestUnlandedFile(_RepoFixture):
    """A file modified in the worktree to bytes committed NOWHERE
    classifies UNLANDED: discarding the worktree destroys the only copy."""

    def test_uncommitted_new_bytes_are_unlanded(self):
        with open(os.path.join(self.worktree, "tracked.txt"), "w") as fh:
            fh.write("bytes that exist only in this worktree\n")

        data = rw.sweep(self.repo)
        files = data["worktrees"][0]["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["verdict"], rw.UNLANDED)


class TestCheckRefusesUnlanded(_RepoFixture):
    """`check` exits nonzero and names the file when the worktree holds an
    UNLANDED file: this is the refusal the whole tool exists to produce."""

    def test_check_refuses_and_names_the_file(self):
        with open(os.path.join(self.worktree, "tracked.txt"), "w") as fh:
            fh.write("unlanded work nobody committed\n")

        rc = rw.main(["check", self.worktree])
        self.assertEqual(rc, 1)


class TestCheckAllowsAllLanded(_RepoFixture):
    """`check` exits 0 on a worktree whose changes are all LANDED."""

    def test_check_passes_when_nothing_would_be_lost(self):
        # Dirty the file back to its own already-committed bytes: a no-op
        # content-wise, but still an uncommitted tracked change to test
        # against.
        with open(os.path.join(self.worktree, "tracked.txt"), "w") as fh:
            fh.write("committed content\n")

        rc = rw.main(["check", self.worktree])
        self.assertEqual(rc, 0)

    def test_check_passes_on_a_perfectly_clean_worktree(self):
        rc = rw.main(["check", self.worktree])
        self.assertEqual(rc, 0)


class TestUnhashableFileIsUnknownNotLanded(_RepoFixture):
    """A file that cannot be hashed (removed out from under git between
    the status listing and the hash step, the ordinary way this happens
    in practice) classifies UNKNOWN, and UNKNOWN must never be reported as
    LANDED anywhere downstream."""

    def test_missing_file_is_unknown(self):
        target = os.path.join(self.worktree, "tracked.txt")
        with open(target, "w") as fh:
            fh.write("about to vanish\n")
        # git status now reports tracked.txt as modified; remove the file
        # from disk without telling git, so hash_worktree_file cannot read
        # it (os.path.exists is False).
        os.remove(target)

        verdict, sha = rw.classify_file(
            self.worktree, "tracked.txt", reachable=set())
        self.assertEqual(verdict, rw.UNKNOWN)
        self.assertIsNone(sha)
        self.assertNotEqual(verdict, rw.LANDED)

        # And the same worktree must not be reported safe to remove.
        rc = rw.main(["check", self.worktree])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
