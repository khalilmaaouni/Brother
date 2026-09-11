#!/usr/bin/env python3
"""preserve_wip's own checks: this script commits and pushes automatically,
so its two compensating controls (never skip hooks, never ship a secret)
need a fixture repo and remote to drive against, never the live estate.

Mirrors the shape of scripts/test_cutover_pack.py: a fixture is built per
test, the script's own functions are called (or run end to end via main()
with sys.argv patched, since main() takes no argv parameter), and the git
calls it makes are spied on rather than re-implemented.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preserve_wip as PW  # noqa: E402


def _git(args, cwd=None):
    subprocess.run(["git"] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


def _init_repo(tmp):
    """A seeded repo plus a local bare 'remote', so a real git push has
    somewhere to land without ever touching the network or the live estate.
    Any local path works as the remote: repo_path() only refuses a URL whose
    last two path segments are khalilmaaouni/brother, and a tempdir never is."""
    repo = os.path.join(tmp, "repo")
    remote = os.path.join(tmp, "remote.git")
    os.makedirs(repo)
    _git(["init", "-q", "."], cwd=repo)
    _git(["config", "user.email", "t@example.com"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("seed\n")
    _git(["add", "README.md"], cwd=repo)
    _git(["commit", "-q", "-m", "seed"], cwd=repo)
    _git(["init", "-q", "--bare", remote], cwd=tmp)
    _git(["remote", "add", "origin", remote], cwd=repo)
    return repo, remote


def _head(repo):
    p = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return p.stdout.strip()


def _run_main(*argv):
    buf = io.StringIO()
    with unittest.mock.patch.object(sys, "argv", ["preserve_wip.py"] + list(argv)), \
         contextlib.redirect_stdout(buf):
        rc = PW.main()
    return rc, buf.getvalue()


class DryRunWritesNothing(unittest.TestCase):
    def test_dry_run_does_not_commit_or_push(self):
        tmp = tempfile.mkdtemp()
        repo, remote = _init_repo(tmp)
        with open(os.path.join(repo, "dirty.txt"), "w") as f:
            f.write("uncommitted\n")
        before = _head(repo)
        rc, out = _run_main("--roots", repo)
        self.assertIn("WOULD PUSH", out)
        self.assertEqual(_head(repo), before, "dry run must not commit")
        p = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                           capture_output=True, text=True)
        self.assertIn("dirty.txt", p.stdout, "dry run must not stage or commit")
        self.assertEqual(rc, 0)


class SecretScanBeforePush(unittest.TestCase):
    def test_planted_secret_in_untracked_file_is_refused_and_named(self):
        tmp = tempfile.mkdtemp()
        repo, remote = _init_repo(tmp)
        secret = "AKIA" + "1234567890ABCDEF"
        with open(os.path.join(repo, "oops.env"), "w") as f:
            f.write("KEY=%s\n" % secret)
        before = _head(repo)
        rc, out = _run_main("--roots", repo, "--apply")
        self.assertIn("REFUSED", out)
        self.assertIn("oops.env", out)
        self.assertNotIn(secret, out, "the matched value must never be printed")
        self.assertEqual(_head(repo), before, "a refused scan must not commit")
        p = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                           capture_output=True, text=True)
        self.assertIn("oops.env", p.stdout, "the file is left as found, unstaged")
        self.assertEqual(rc, 1)


class CommitNeverSkipsHooks(unittest.TestCase):
    def test_clean_worktree_is_committed_without_no_verify(self):
        tmp = tempfile.mkdtemp()
        repo, remote = _init_repo(tmp)
        with open(os.path.join(repo, "clean.txt"), "w") as f:
            f.write("nothing private here\n")

        calls = []
        real_git = PW.git

        def spy(args, cwd=None):
            calls.append(list(args))
            return real_git(args, cwd=cwd)

        with unittest.mock.patch.object(PW, "git", side_effect=spy):
            rc, out = _run_main("--roots", repo, "--apply")

        commit_calls = [c for c in calls if "commit" in c]
        self.assertEqual(len(commit_calls), 1, calls)
        self.assertNotIn("--no-verify", commit_calls[0])
        self.assertIn("PUSHED", out)
        self.assertEqual(rc, 0)
        p = subprocess.run(["git", "-C", remote, "branch", "-a"],
                           capture_output=True, text=True)
        self.assertIn("archive/wip-", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
