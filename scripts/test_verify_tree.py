"""verify_tree.sh must clear a stale registration and fail closed on a bad ref.

Row M2 of the 2026-09-07 reflection: `rm -rf` of a verify worktree left
git's registration behind, the next `git worktree add` at that path failed,
and the chain that kept going after the failed step ran on main instead.
The two properties under test: (1) a stale registration at PATH is pruned
and the add still succeeds; (2) a bad ref exits non-zero and leaves no
directory at PATH.
"""
import os
import subprocess
import sys
import unittest

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                      % os.path.basename(__file__))

import tempfile  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "verify_tree.sh")


def make_repo():
    """A tiny real git repository with one commit on main."""
    root = tempfile.mkdtemp()
    run = lambda *a: subprocess.run(a, cwd=root, check=True,  # noqa: E731
                                     capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.name", "Test")
    run("git", "config", "user.email", "test@example.com")
    with open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as fh:
        fh.write("one\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "seed")
    run("git", "branch", "-M", "main")
    return root


def run_verify_tree(repo, target, ref):
    return subprocess.run(["sh", SCRIPT, target, ref], cwd=repo,
                           capture_output=True, text=True)


class StaleRegistrationIsPruned(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        # a real macOS temp path so the case check in the script (which
        # compares against `git worktree list`'s own printed path) matches
        # without a /tmp -> /private/tmp symlink surprise.
        self.parent = tempfile.mkdtemp()
        self.target = os.path.join(self.parent, "wt")

    def test_stale_registration_is_pruned_and_add_succeeds(self):
        first = run_verify_tree(self.repo, self.target, "main")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(os.path.isdir(self.target))

        # Simulate the exact mistake: rm -rf the worktree directory without
        # telling git, leaving the registration behind.
        import shutil
        shutil.rmtree(self.target)
        listing = subprocess.run(["git", "worktree", "list"], cwd=self.repo,
                                  capture_output=True, text=True)
        self.assertIn(self.target, listing.stdout)  # registration survives the rm -rf

        second = run_verify_tree(self.repo, self.target, "main")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(os.path.isdir(self.target))
        # stdout carries exactly the path, nothing else, so cd "$(...)" works
        self.assertEqual(second.stdout.strip(), self.target)

    def test_resolved_commit_is_printed(self):
        head = subprocess.run(["git", "rev-parse", "main"], cwd=self.repo,
                               capture_output=True, text=True).stdout.strip()
        result = run_verify_tree(self.repo, self.target, "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(head, result.stderr)


class BadRefFailsClosed(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.parent = tempfile.mkdtemp()
        self.target = os.path.join(self.parent, "wt")

    def test_bad_ref_exits_nonzero_and_creates_no_directory(self):
        result = run_verify_tree(self.repo, self.target, "no-such-ref-xyz")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.target))


class UsageIsRejected(unittest.TestCase):
    def test_wrong_arg_count_exits_nonzero(self):
        result = subprocess.run(["sh", SCRIPT, "/only/one/arg"],
                                 capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
