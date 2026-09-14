#!/usr/bin/env python3
"""brother_worktree_census.py's own checks, driven against real git state.

Mirrors scripts/test_reclaim_worktrees.py's shape: sys.path insert, import
the module under test directly, a tempdir with a bare remote plus worktrees
built by hand, unittest.TestCase, verbosity=2. git status/rev-list/du are not
worth mocking without reimplementing them, and mocking here would only prove
the mock is self-consistent, not that the script is.

The one property worth pinning explicitly, per this estate's own recorded
finding (a-fresh-worktrees-mtime-is-checkout-time): last_touch_utc must come
from the newest TRACKED FILE's mtime, never the worktree directory's own
mtime. test_last_touch_reads_tracked_file_mtime_not_directory_mtime forces
the two apart with os.utime and asserts the census followed the file.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_worktree_census as C  # noqa: E402


def run_git(args, cwd=None):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                   capture_output=True, text=True)


class WorktreeCensusFixture(unittest.TestCase):
    """One bare remote, one main checkout, three worktrees:
    wt-clean (pushed, nothing outstanding), wt-ahead-and-dirty (upstream
    configured but ahead of it, plus an uncommitted file), wt-no-upstream
    (branch with no upstream ever configured)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="worktree-census-test-")
        self.remote = os.path.join(self.tmp, "remote.git")
        run_git(["init", "--bare", "-q", self.remote])
        self.root = os.path.join(self.tmp, "root")
        run_git(["clone", "-q", self.remote, self.root])
        run_git(["-C", self.root, "config", "user.email", "t@example.com"])
        run_git(["-C", self.root, "config", "user.name", "t"])
        with open(os.path.join(self.root, "a.txt"), "w") as f:
            f.write("root\n")
        run_git(["-C", self.root, "add", "a.txt"])
        run_git(["-C", self.root, "commit", "-q", "-m", "init"])
        base_branch = subprocess.run(
            ["git", "-C", self.root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        run_git(["-C", self.root, "push", "-q", "-u", "origin", base_branch])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _worktree(self, name, branch):
        path = os.path.join(self.tmp, name)
        run_git(["-C", self.root, "worktree", "add", "-q", "-b", branch, path])
        run_git(["-C", path, "config", "user.email", "t@example.com"])
        run_git(["-C", path, "config", "user.name", "t"])
        return os.path.realpath(path)

    def _commit_file(self, path, name, content):
        fp = os.path.join(path, name)
        with open(fp, "w") as f:
            f.write(content)
        run_git(["-C", path, "add", name])
        run_git(["-C", path, "commit", "-q", "-m", "wt commit " + name])
        return fp

    def _rows_by_path(self):
        rows = C.census(os.path.realpath(self.root))
        return {r["path"]: r for r in rows}

    def test_clean_pushed_worktree_reports_not_dirty_and_not_unpushed(self):
        path = self._worktree("wt-clean", "wt-clean-branch")
        self._commit_file(path, "b.txt", "clean\n")
        run_git(["-C", path, "push", "-q", "-u", "origin", "wt-clean-branch"])
        row = self._rows_by_path()[path]
        self.assertEqual(row["branch"], "wt-clean-branch")
        self.assertFalse(row["dirty"], row)
        self.assertIs(row["unpushed_commits"], False, row)
        self.assertNotEqual(row["disk_usage"], "NO-DATA", row)

    def test_ahead_of_upstream_and_dirty_worktree_reports_both(self):
        path = self._worktree("wt-ahead-dirty", "wt-ahead-dirty-branch")
        self._commit_file(path, "b.txt", "first\n")
        run_git(["-C", path, "push", "-q", "-u", "origin", "wt-ahead-dirty-branch"])
        self._commit_file(path, "c.txt", "second, never pushed\n")
        with open(os.path.join(path, "d.txt"), "w") as f:
            f.write("uncommitted\n")
        row = self._rows_by_path()[path]
        self.assertTrue(row["dirty"], row)
        self.assertIs(row["unpushed_commits"], True, row)

    def test_no_upstream_configured_reports_no_data_never_a_guess(self):
        path = self._worktree("wt-no-upstream", "wt-no-upstream-branch")
        self._commit_file(path, "b.txt", "never pushed\n")
        row = self._rows_by_path()[path]
        self.assertEqual(row["unpushed_commits"], "NO-DATA", row)

    def test_last_touch_reads_tracked_file_mtime_not_directory_mtime(self):
        # A worktree inherits every tracked file from the base commit
        # (a.txt here), and a fresh checkout gives ALL of them "now" as
        # their mtime -- so a real test must set every OTHER tracked file
        # old first, then bump exactly one file to a distinct "touch" time,
        # and set the directory's own mtime to something else again, to
        # prove last_touch follows the touched FILE and not the directory.
        path = self._worktree("wt-mtime", "wt-mtime-branch")
        tracked = self._commit_file(path, "b.txt", "tracked\n")
        stale = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
        touch = datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc).timestamp()
        future = datetime.datetime(2035, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
        code, out, _ = C.run(["git", "-C", path, "ls-files"])
        self.assertEqual(code, 0, out)
        for rel in out.splitlines():
            os.utime(os.path.join(path, rel), (stale, stale))
        os.utime(tracked, (touch, touch))          # the one real "last touch"
        os.utime(path, (future, future))           # directory mtime: ignored
        row = self._rows_by_path()[path]
        got = datetime.datetime.fromisoformat(row["last_touch_utc"])
        self.assertEqual(int(got.timestamp()), int(touch), row)
        self.assertNotEqual(int(got.timestamp()), int(future), row)
        self.assertNotEqual(int(got.timestamp()), int(stale), row)

    def test_json_lines_output_is_one_object_per_row(self):
        self._worktree("wt-json", "wt-json-branch")
        buf_path = os.path.join(self.tmp, "out.jsonl")
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch.object(
                    sys, "argv",
                    ["brother_worktree_census.py", "--repo", self.root, "--out", buf_path]):
                rc = C.main()
        self.assertEqual(rc, 0)
        with open(buf_path) as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        self.assertGreaterEqual(len(lines), 2, lines)  # main checkout + wt-json
        for line in lines:
            obj = json.loads(line)  # each line parses on its own
            self.assertIn("path", obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)
