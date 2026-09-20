"""MD-1: preflight_snapshot must tag the real HEAD and never touch the
working tree; a rigged bad commit after the tag must be fully revertable."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preflight_snapshot as P  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True, check=True)


def _init_repo(tmp):
    repo = os.path.join(tmp, "scratch-repo")
    os.makedirs(repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "a@b.c")
    _git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "f.txt"), "w") as fh:
        fh.write("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


class PreflightSnapshot(unittest.TestCase):
    def test_tags_the_real_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            real_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
            tag, sha = P.snapshot(repo, label="test-a")
            self.assertEqual(sha, real_head)
            # -a made this an annotated tag: `rev-parse <tag>` returns the
            # tag OBJECT's own sha, not the commit -- dereference with
            # ^{commit} to get the commit it actually points at.
            tagged_sha = _git(repo, "rev-parse", tag + "^{commit}").stdout.strip()
            self.assertEqual(tagged_sha, real_head)

    def test_a_bad_commit_after_the_tag_is_fully_revertable(self):
        """The real point: rig a bad autonomous change, prove reset --hard
        to the tag removes it completely."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            tag, good_sha = P.snapshot(repo, label="test-b")

            with open(os.path.join(repo, "f.txt"), "w") as fh:
                fh.write("BAD AUTONOMOUS CHANGE\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "bad")
            bad_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
            self.assertNotEqual(bad_sha, good_sha)
            with open(os.path.join(repo, "f.txt")) as fh:
                self.assertIn("BAD", fh.read())

            _git(repo, "reset", "--hard", tag)
            restored_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
            self.assertEqual(restored_sha, good_sha)
            with open(os.path.join(repo, "f.txt")) as fh:
                self.assertEqual(fh.read(), "base\n")

    def test_a_non_git_path_fails_loudly_not_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_a_repo = os.path.join(tmp, "plain-dir")
            os.makedirs(not_a_repo)
            with self.assertRaises(RuntimeError):
                P.snapshot(not_a_repo)

    def test_working_tree_is_never_touched(self):
        """snapshot() is read-only against the tree: an uncommitted file
        present when it runs is still there, unstaged, afterward."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(tmp)
            with open(os.path.join(repo, "untracked.txt"), "w") as fh:
                fh.write("scratch\n")
            P.snapshot(repo, label="test-c")
            status = _git(repo, "status", "--porcelain").stdout
            self.assertIn("untracked.txt", status)


if __name__ == "__main__":
    unittest.main()
