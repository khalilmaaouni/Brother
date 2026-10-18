#!/usr/bin/env python3
"""C11: the cut never writes into the shared checkout.

During the 1.1.5 cut, reproduce_export.py fetched the new tag into ~/Brother,
a checkout other sessions share, because main() fell back to PUBLIC_CHECKOUT
when --public was not given (cut.py never passes it). The fix routes that
default through default_public(), which answers the checkout the script runs
from (the cut's own worktree).

These cases drive main() itself, the entry point cut.py runs, with real git:
HOME points at a fixture folder holding a sentinel Brother checkout, EP.ROOT
points at a fixture cut worktree, and every git command reproduce_export runs
is recorded with the directory it ran in. The fetch must land in the cut's
worktree, and nothing under the sentinel may change.

Edge named and left out of scope: when the cut's worktree is a git worktree
of the shared checkout, the two share one ref store, so a fetched tag ref is
visible from both. That is git's design, not a write into the shared
checkout's working tree; the fixture worktree here is its own repository.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import tmp_sandbox as _tmp
    _tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

# HOME must point at the fixture BEFORE reproduce_export is imported:
# PUBLIC_CHECKOUT is expanded from it at import time, which is exactly the
# value the defect fell back to.
FIXTURE_HOME = tempfile.mkdtemp(prefix="c11-home-")
os.environ["HOME"] = FIXTURE_HOME
SENTINEL = os.path.join(FIXTURE_HOME, "Brother")

import reproduce_export as R  # noqa: E402

TAG = "v9.9.9"


def _env():
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                   text=True, env=_env(), check=True)


def _repo(path, files):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    for rel, text in files.items():
        full = os.path.join(path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)
    _git(path, "add", "-A")
    _git(path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return path


def _snapshot(root):
    """Every path under root with its mtime and size: a fetch that writes a
    tag ref, FETCH_HEAD or a pack changes at least one entry."""
    seen = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(base, name)
            st = os.lstat(full)
            seen[os.path.relpath(full, root)] = (st.st_mtime_ns, st.st_size)
    return seen


def _git_dir_of(cmd, kwargs):
    if "-C" in cmd:
        return os.path.realpath(cmd[cmd.index("-C") + 1])
    return os.path.realpath(kwargs.get("cwd") or os.getcwd())


class CutFetchPath(unittest.TestCase):
    def setUp(self):
        if os.path.isdir(SENTINEL):
            shutil.rmtree(SENTINEL)
        _repo(SENTINEL, {"STATE.md": "shared checkout, other sessions\n"})
        self.tmp = tempfile.mkdtemp(prefix="c11-")
        remote = os.path.join(self.tmp, "remote.git")
        os.makedirs(remote)
        _git(remote, "init", "-q", "--bare")
        seed = _repo(os.path.join(self.tmp, "seed"),
                     {"scripts/a.py": "print('one')\n"})
        _git(seed, "-c", "tag.gpgsign=false", "tag", TAG)
        _git(seed, "push", "-q", remote, "HEAD:refs/heads/main",
             "refs/tags/%s" % TAG)
        self.remote = remote
        self.cut = _repo(os.path.join(self.tmp, "cut-worktree"),
                         {"README.md": "the cut's own worktree\n"})
        self.export = os.path.join(self.tmp, "export")
        os.makedirs(os.path.join(self.export, "scripts"))
        with open(os.path.join(self.export, "scripts", "a.py"), "w") as fh:
            fh.write("print('one')\n")
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, real):
        def run(cmd, *a, **kw):
            self.calls.append((cmd[3] if len(cmd) > 3 and cmd[1] == "-C"
                               else cmd[1], _git_dir_of(cmd, kw)))
            kw["env"] = dict(kw.get("env") or os.environ,
                             GIT_CONFIG_GLOBAL=os.devnull,
                             GIT_CONFIG_NOSYSTEM="1")
            return real(cmd, *a, **kw)
        return run

    def _main(self, extra=(), root=None):
        real_fetch = R.fetch_and_resolve_tag
        saved = (R.EP.ROOT, R.EP.load_allowlist, R.source_tree,
                 R.EP.build_export_tree, R.fetch_and_resolve_tag, R._run)
        recorder = self._record(subprocess.run)

        def fetch(public, tag, remote, runner=None, **kw):
            return real_fetch(public, tag, remote, runner=recorder, **kw)

        def run(cmd, cwd=None):
            return recorder(cmd, cwd=cwd, capture_output=True)

        try:
            R.EP.ROOT = root or self.cut
            R.EP.load_allowlist = lambda p=None: ["scripts"]
            R.source_tree = lambda rev, root=None: self.export
            R.EP.build_export_tree = lambda dest, al, root=None: (
                shutil.copytree(self.export, dest, dirs_exist_ok=True)
                and ["scripts/a.py"])
            R.fetch_and_resolve_tag = fetch
            R._run = run
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = R.main(["--tag", TAG, "--remote", self.remote,
                               "--source-rev", "HEAD"] + list(extra))
        finally:
            (R.EP.ROOT, R.EP.load_allowlist, R.source_tree,
             R.EP.build_export_tree, R.fetch_and_resolve_tag,
             R._run) = saved
        return code, out.getvalue()

    def _fetch_dirs(self):
        return [d for verb, d in self.calls if verb == "fetch"]

    def test_the_fetch_runs_in_the_cut_worktree(self):
        code, out = self._main()
        self.assertEqual(self._fetch_dirs(), [os.path.realpath(self.cut)],
                         "without --public the tag must be fetched in the "
                         "cut's own worktree, never %s\n%s" % (SENTINEL, out))
        self.assertEqual(code, 0, out)

    def test_the_shared_checkout_is_untouched(self):
        before = _snapshot(SENTINEL)
        code, out = self._main()
        self.assertEqual(_snapshot(SENTINEL), before,
                         "a file under the shared checkout changed\n" + out)
        sentinel = os.path.realpath(SENTINEL)
        touched = [(v, d) for v, d in self.calls
                   if d == sentinel or d.startswith(sentinel + os.sep)]
        self.assertEqual(touched, [], "git ran in the shared checkout")
        self.assertEqual(code, 0, out)

    def test_an_explicit_public_wins(self):
        other = _repo(os.path.join(self.tmp, "explicit-public"),
                      {"x.txt": "x\n"})
        code, out = self._main(["--public", other])
        self.assertEqual(self._fetch_dirs(), [os.path.realpath(other)], out)
        self.assertEqual(code, 0, out)
        self.assertEqual(R.default_public(other, self.cut), other)

    def test_a_bad_here_refuses_and_an_empty_public_is_not_given(self):
        self.assertEqual(R.default_public("", self.cut), self.cut)
        self.assertEqual(R.default_public(None, self.cut), self.cut)
        a_file = os.path.join(self.cut, "README.md")
        relative = os.path.relpath(self.cut)  # exists, but not absolute
        self.assertTrue(os.path.isdir(relative))
        for bad in (a_file, relative, os.path.join(self.tmp, "absent"),
                    "", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                R.default_public(None, bad)

    def test_main_reads_a_bad_here_as_no_data(self):
        code, out = self._main(root=os.path.join(self.tmp, "absent"))
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertEqual(self._fetch_dirs(), [], out)

if __name__ == "__main__":
    unittest.main()
