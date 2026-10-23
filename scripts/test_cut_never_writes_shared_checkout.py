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
import builtins
import contextlib
import hashlib
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

#: I18: the store artifact's own file name. A cut's run folder holds exactly
#: this file, and no write of it may land under the shared checkout.
STORE_NAME = "store.json"


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


def _digest(path):
    """The hex sha256 of a regular file's bytes, and the empty string for a
    directory, a symlink, a special file, or anything that cannot be read.
    Never raises."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return ""
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (OSError, ValueError, TypeError):
        return ""


def _snapshot(root):
    """Every path under root with its mtime, size, ctime and content digest:
    a fetch that writes a tag ref, FETCH_HEAD or a pack changes at least one
    entry, and a write that restores mtime and size still moves the digest
    (I18), which a size-and-mtime-only snapshot misses. One entry per path,
    keyed by its path relative to root. A path that vanishes between the walk
    and the lstat is skipped, never raised."""
    if isinstance(root, bool) or not isinstance(root, str):
        raise ValueError("_snapshot needs a directory path, not %s"
                         % type(root).__name__)
    seen = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(base, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            seen[os.path.relpath(full, root)] = (
                st.st_mtime_ns, st.st_size, st.st_ctime_ns, _digest(full))
    return seen


def _make_read_only(root):
    """Chmod every directory under root to 0o555 and every other entry to
    0o444, so a write attempt into the read-only fixture raises
    PermissionError on its own, whatever the write audit records. Safe on a
    tree that is already read only, and returns None."""
    if isinstance(root, bool) or not isinstance(root, str):
        raise ValueError("_make_read_only needs a directory path, not %s"
                         % type(root).__name__)
    if not os.path.isdir(root):
        raise ValueError("_make_read_only needs an existing directory: %r"
                         % (root,))
    for base, _dirs, files in os.walk(root):
        try:
            os.chmod(base, 0o555)
        except OSError:
            pass
        for name in files:
            try:
                os.chmod(os.path.join(base, name), 0o444)
            except OSError:
                pass
    return None


@contextlib.contextmanager
def _write_audit(root):
    """A context manager over a list of every write destination seen while it
    is open, each recorded as os.path.realpath(dest). It wraps
    builtins.open, io.open, os.replace, os.rename and shutil.copyfile for the
    life of the with block, restores every wrapper on exit, and appends a
    CHANGED line when root's own snapshot still moved, so a write that
    bypasses Python is named too."""
    if isinstance(root, bool) or not isinstance(root, str):
        raise ValueError("_write_audit needs a directory path, not %s"
                         % type(root).__name__)
    if not os.path.isdir(root):
        raise ValueError("_write_audit needs an existing directory: %r"
                         % (root,))
    writes = []
    before = _snapshot(root)
    real_open = builtins.open
    real_replace = os.replace
    real_rename = os.rename
    real_copyfile = shutil.copyfile

    def watched_open(path, *args, **kw):
        mode = kw.get("mode", args[0] if args else "r")
        if (isinstance(path, (str, bytes)) and isinstance(mode, str)
                and any(flag in mode for flag in "wax+")):
            writes.append(os.path.realpath(path))
        return real_open(path, *args, **kw)

    def watched_replace(src, dst, *args, **kw):
        writes.append(os.path.realpath(dst))
        return real_replace(src, dst, *args, **kw)

    def watched_rename(src, dst, *args, **kw):
        writes.append(os.path.realpath(dst))
        return real_rename(src, dst, *args, **kw)

    def watched_copyfile(src, dst, *args, **kw):
        writes.append(os.path.realpath(dst))
        return real_copyfile(src, dst, *args, **kw)

    builtins.open = watched_open
    io.open = watched_open
    os.replace = watched_replace
    os.rename = watched_rename
    shutil.copyfile = watched_copyfile
    try:
        yield writes
    finally:
        builtins.open = real_open
        io.open = real_open
        os.replace = real_replace
        os.rename = real_rename
        shutil.copyfile = real_copyfile
        if _snapshot(root) != before:
            writes.append("CHANGED " + os.path.realpath(root))


def _check_run_dir(run_dir):
    """The store hook's run folder contract (I18): BROTHER_RUN_DIR must name
    an absolute directory that is not the shared checkout and not under it,
    or the cut refuses. A missing, empty, relative, non-string,
    non-directory or inside-the-sentinel value raises ValueError before
    anything is written: never a crash out of a raw os call and never a
    silent safe default."""
    if run_dir is None:
        raise ValueError("BROTHER_RUN_DIR is not set: the store hook has no "
                         "run folder to write into")
    if isinstance(run_dir, bool) or not isinstance(run_dir, str):
        raise ValueError("BROTHER_RUN_DIR must be a string path, not %s"
                         % type(run_dir).__name__)
    if run_dir == "":
        raise ValueError("BROTHER_RUN_DIR is empty")
    if not os.path.isabs(run_dir):
        raise ValueError("BROTHER_RUN_DIR must be an absolute path: %r"
                         % (run_dir,))
    real = os.path.realpath(run_dir)
    sentinel = os.path.realpath(SENTINEL)
    if real == sentinel or real.startswith(sentinel + os.sep):
        raise ValueError("BROTHER_RUN_DIR names the shared checkout: %r"
                         % (run_dir,))
    if not os.path.isdir(real):
        raise ValueError("BROTHER_RUN_DIR is not an existing directory: %r"
                         % (run_dir,))
    return real


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
        # I18: the run folder the store hook is handed, absolute and under
        # this test's own temporary tree, so every case that drives the cut
        # has a valid BROTHER_RUN_DIR and no case has to invent one.
        self.run_dir = os.path.realpath(tempfile.mkdtemp(prefix="c11-run-"))
        os.environ["BROTHER_RUN_DIR"] = self.run_dir

    def tearDown(self):
        # I18: a read-only fixture is restored and the run folder this case
        # set is cleared, so no case leaks permissions or environment.
        for base, dirs, files in os.walk(SENTINEL):
            try:
                os.chmod(base, 0o755)
            except OSError:
                pass
            for name in dirs:
                try:
                    os.chmod(os.path.join(base, name), 0o755)
                except OSError:
                    pass
            for name in files:
                try:
                    os.chmod(os.path.join(base, name), 0o644)
                except OSError:
                    pass
        os.environ.pop("BROTHER_RUN_DIR", None)
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
        # I18: the store hook's run folder contract, routed through the one
        # entry point the cut uses: a bad BROTHER_RUN_DIR is refused here,
        # before any cut work, never read as the safe case.
        run_dir = _check_run_dir(os.environ.get("BROTHER_RUN_DIR"))
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
        # I18: what the store hook does once the cut it runs under has
        # succeeded: one artifact, written through the run folder it was
        # handed and nowhere else. The write goes through whatever audit the
        # guard has open, so a destination under the shared checkout is NAMED
        # rather than inferred from a changed mtime.
        if code == 0:
            with open(os.path.join(run_dir, STORE_NAME), "w") as fh:
                fh.write("store\n")
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

    def _refuses_run_dir(self, run_dir):
        """BROTHER_RUN_DIR's contract, driven through the cut: None, an
        empty string, a missing path, a relative path, a regular file, a
        value that is not a string at all, or a symlink to the shared
        checkout is refused (ValueError before any write, or a NO-DATA result
        at exit 2) with the shared checkout unchanged. True only then."""
        before = _snapshot(SENTINEL)
        saved = os.environ.get("BROTHER_RUN_DIR")
        code, out = 0, ""
        try:
            if run_dir is None:
                os.environ.pop("BROTHER_RUN_DIR", None)
            elif isinstance(run_dir, str):
                os.environ["BROTHER_RUN_DIR"] = run_dir
            else:
                # os.environ holds strings only. Any other type goes to the
                # same contract function the cut calls, where it is refused,
                # never coerced to a string first.
                try:
                    _check_run_dir(run_dir)
                except ValueError:
                    return _snapshot(SENTINEL) == before
                raise AssertionError(
                    "the run dir contract accepted %r, which is not a path"
                    % (run_dir,))
            try:
                code, out = self._main()
            except ValueError:
                return _snapshot(SENTINEL) == before
        finally:
            if saved is None:
                os.environ.pop("BROTHER_RUN_DIR", None)
            else:
                os.environ["BROTHER_RUN_DIR"] = saved
        return code == 2 and "NO-DATA" in out and _snapshot(SENTINEL) == before

    def test_the_store_hook_writes_under_the_run_folder_only(self):
        run_dir = os.path.realpath(tempfile.mkdtemp(prefix="c11-store-"))
        os.environ["BROTHER_RUN_DIR"] = run_dir
        try:
            _make_read_only(SENTINEL)
            before = _snapshot(SENTINEL)
            with _write_audit(SENTINEL) as writes:
                code, out = self._main()
            self.assertEqual(code, 0, out)
            store = os.path.join(run_dir, STORE_NAME)
            self.assertTrue(os.path.isfile(store),
                            "the run folder %s holds no %s"
                            % (run_dir, STORE_NAME))
            self.assertGreater(os.path.getsize(store), 0,
                               "the %s artifact is empty" % STORE_NAME)
            sentinel = os.path.realpath(SENTINEL)
            bad = [p for p in writes
                   if p == sentinel or p.startswith(sentinel + os.sep)]
            self.assertEqual(bad, [],
                             "a write landed under the shared checkout: %s"
                             % ", ".join(bad))
            self.assertTrue(
                any(p == run_dir or p.startswith(run_dir + os.sep)
                    for p in writes),
                "no audited path lies under the run folder: %r" % (writes,))
            self.assertEqual(_snapshot(SENTINEL), before,
                             "a file under the shared checkout changed")
        finally:
            os.environ["BROTHER_RUN_DIR"] = self.run_dir

    def test_a_write_that_restores_mtime_and_size_is_still_seen(self):
        work = tempfile.mkdtemp(prefix="c11-edit-")
        target = os.path.join(work, "f.txt")
        with open(target, "w") as fh:
            fh.write("aaaa")
        before = _snapshot(work)
        rel = os.path.relpath(target, work)
        st = os.stat(target)
        with open(target, "w") as fh:
            fh.write("bbbb")
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        after = _snapshot(work)
        self.assertEqual(after[rel][:2], before[rel][:2],
                         "the fixture did not restore mtime and size, so the "
                         "digest half is not what this case tests")
        self.assertNotEqual(after, before,
                            "a same-length edit with a restored mtime left "
                            "the snapshot equal, so the digest is not read")

    def test_a_bad_run_dir_is_refused_and_never_writes_the_checkout(self):
        missing = os.path.join(self.tmp, "absent-run-dir")
        relative = os.path.relpath(self.tmp)
        a_file = os.path.join(self.tmp, "seed", "scripts", "a.py")
        sentinel = os.path.realpath(SENTINEL)
        for bad in (None, "", missing, relative, a_file, True):
            with self.subTest(run_dir=bad):
                before = _snapshot(SENTINEL)
                with _write_audit(SENTINEL) as writes:
                    self.assertTrue(
                        self._refuses_run_dir(bad),
                        "the cut accepted BROTHER_RUN_DIR %r" % (bad,))
                self.assertEqual(_snapshot(SENTINEL), before,
                                 "BROTHER_RUN_DIR %r changed the shared "
                                 "checkout" % (bad,))
                under = [p for p in writes
                         if p == sentinel or p.startswith(sentinel + os.sep)]
                self.assertEqual(under, [],
                                 "a write landed under the shared checkout "
                                 "for BROTHER_RUN_DIR %r: %r" % (bad, under))

    def test_a_run_dir_symlink_to_the_sentinel_is_refused(self):
        link = os.path.join(self.tmp, "sentinel-link")
        os.symlink(os.path.realpath(SENTINEL), link)
        before = _snapshot(SENTINEL)
        self.assertTrue(self._refuses_run_dir(link),
                        "a symlink to the shared checkout was accepted")
        self.assertEqual(_snapshot(SENTINEL), before)

if __name__ == "__main__":
    unittest.main()
