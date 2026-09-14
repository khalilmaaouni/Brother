"""lane_resume must read a worktree without touching it and leave a resume kit.

Row M9 of the 2026-09-07 reflection: four lanes died with uncommitted work
only in their worktrees, and recovery needed a hand-built diff each time.
The property under test: given a worktree with one modified tracked file
and one untracked file, the tool writes a patch that reproduces the
modification, a copy of the untracked file, and a report naming both --
while leaving the source worktree byte-for-byte unchanged.
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_resume as L  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                      % os.path.basename(__file__))

import tempfile  # noqa: E402


def make_repo():
    root = tempfile.mkdtemp()
    run = lambda *a: subprocess.run(a, cwd=root, check=True,  # noqa: E731
                                     capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.name", "Test")
    run("git", "config", "user.email", "test@example.com")
    with open(os.path.join(root, "tracked.txt"), "w", encoding="utf-8") as fh:
        fh.write("original\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "seed")
    run("git", "checkout", "-q", "-b", "lane/work")

    # one modified tracked file, uncommitted
    with open(os.path.join(root, "tracked.txt"), "w", encoding="utf-8") as fh:
        fh.write("original\nchanged\n")

    # one untracked file
    with open(os.path.join(root, "scratch.txt"), "w", encoding="utf-8") as fh:
        fh.write("scratch content\n")

    return root


class NotAWorktreeRefusesWithNoData(unittest.TestCase):
    def test_a_plain_directory_refuses(self):
        plain = tempfile.mkdtemp()
        out = tempfile.mkdtemp()
        code = L.main([plain, "--out", out])
        self.assertEqual(code, 2)
        self.assertEqual(os.listdir(out), [])


class ReadsWithoutModifyingAndLeavesAResumeKit(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.out = tempfile.mkdtemp()

    def _status(self):
        return subprocess.run(["git", "status", "--porcelain"], cwd=self.repo,
                              capture_output=True, text=True).stdout

    def test_worktree_is_untouched(self):
        before = self._status()
        code = L.main([self.repo, "--out", self.out])
        self.assertEqual(code, 0)
        after = self._status()
        self.assertEqual(before, after)

    def test_diff_patch_reproduces_the_modification(self):
        code = L.main([self.repo, "--out", self.out])
        self.assertEqual(code, 0)
        with open(os.path.join(self.out, "diff.patch"), encoding="utf-8") as fh:
            patch = fh.read()
        self.assertIn("tracked.txt", patch)
        self.assertIn("+changed", patch)

    def test_untracked_file_is_listed_and_copied(self):
        code = L.main([self.repo, "--out", self.out])
        self.assertEqual(code, 0)
        with open(os.path.join(self.out, "untracked.txt"), encoding="utf-8") as fh:
            listed = fh.read().splitlines()
        self.assertEqual(listed, ["scratch.txt"])
        copied = os.path.join(self.out, "untracked", "scratch.txt")
        self.assertTrue(os.path.isfile(copied))
        with open(copied, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "scratch content\n")

    def test_report_names_branch_commit_upstream_and_apply_command(self):
        code = L.main([self.repo, "--out", self.out])
        self.assertEqual(code, 0)
        with open(os.path.join(self.out, "REPORT.md"), encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("lane/work", report)
        self.assertIn("(none)", report)  # no upstream configured in this fixture
        self.assertIn("git checkout -b", report)
        self.assertIn("git apply", report)
        self.assertIn("diff.patch", report)


class Night0912LaneResume(unittest.TestCase):
    def test_staged_only_changes_are_in_patch(self):
        repo = make_repo()
        run = lambda *a: subprocess.run(a, cwd=repo, check=True,  # noqa: E731
                                        capture_output=True, text=True)
        # tracked.txt is already modified in the working tree (make_repo);
        # staging it here (no further edit) makes it a staged-only change.
        run("git", "add", "tracked.txt")
        out = tempfile.mkdtemp()

        code = L.main([repo, "--out", out])
        self.assertEqual(code, 0)
        with open(os.path.join(out, "diff.patch"), encoding="utf-8") as fh:
            patch = fh.read()
        with open(os.path.join(out, "REPORT.md"), encoding="utf-8") as fh:
            report = fh.read()
        self.assertTrue(patch.strip(), "staged-only change must be in diff.patch")
        self.assertIn("tracked.txt", patch)
        self.assertIn("modified/staged tracked file(s): 1", report)


if __name__ == "__main__":
    unittest.main()
