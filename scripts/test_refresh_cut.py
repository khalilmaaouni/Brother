#!/usr/bin/env python3
"""Drive refresh_cut's dirty-tree guard backwards, against a real throwaway
git repository, never the live hub.

THE DEFECT this proves closed (row measured 2026-09-05, X7 second side on
the 1.0.4 closeout): refresh_cut.py used to write the release note by
stamping whatever HEAD happened to be, with no regard for files still sitting
uncommitted beside it. Anything landing in the SAME later commit as the note
(a regenerated bundle, SYSTEM.md, a product's CHECKSUMS.sha256) then moved
the tree past the revision the note named, and rebuilding the export from
that named revision came up short exactly those files.

Real git on purpose: the guard's whole claim is about what `git status
--porcelain` and `git add` actually do to a real index, so the reading and
writing paths here are exercised, never stubbed.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refresh_cut as RC  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

VERSION = "9.9.9"


def _git(args, cwd, check=True):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True)
    if check and proc.returncode != 0:
        raise AssertionError("fixture git failed: %s: %s"
                             % (args, proc.stderr))
    return proc


def _make_repo():
    """A real git repository with one committed file, clean."""
    tmp = tempfile.mkdtemp(prefix="refresh-cut-test-")
    with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("fixture\n")
    for args in (["init", "-q"],
                ["config", "user.email", "t@example.com"],
                ["config", "user.name", "T"],
                ["add", "-A"],
                ["commit", "-q", "-m", "seed"]):
        _git(args, tmp)
    return tmp


def _status(tmp):
    return _git(["status", "--porcelain"], tmp).stdout


class ADirtyTreeRefusesNamingThePath(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_repo()

    def test_an_unrelated_uncommitted_file_refuses(self):
        with open(os.path.join(self.tmp, "scratch.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("uncommitted\n")
        code, lines = RC.refuse_if_dirty(VERSION, None, root=self.tmp)
        self.assertEqual(code, RC.EXIT_NODATA)
        self.assertTrue(any("scratch.txt" in l for l in lines), lines)

    def test_an_unrelated_modification_to_a_tracked_file_refuses(self):
        with open(os.path.join(self.tmp, "README.md"), "a",
                 encoding="utf-8") as fh:
            fh.write("more\n")
        code, lines = RC.refuse_if_dirty(VERSION, None, root=self.tmp)
        self.assertEqual(code, RC.EXIT_NODATA)
        self.assertTrue(any("README.md" in l for l in lines), lines)

    def test_a_clean_tree_proceeds(self):
        code, lines = RC.refuse_if_dirty(VERSION, None, root=self.tmp)
        self.assertIsNone(code)

    def test_the_two_release_files_alone_do_not_count_as_dirty(self):
        # Exactly the shape a re-run leaves behind: the note and manifest
        # already sitting on disk, unstaged, from an earlier attempt.
        for rel in RC.release_paths(VERSION):
            full = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("stub\n")
        code, lines = RC.refuse_if_dirty(VERSION, None, root=self.tmp)
        self.assertIsNone(code, lines)


class AllowDirtyRecordsTheReasonInTheNote(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_repo()
        with open(os.path.join(self.tmp, "scratch.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("uncommitted\n")

    def test_no_reason_still_refuses(self):
        code, lines = RC.refuse_if_dirty(VERSION, "", root=self.tmp)
        self.assertEqual(code, RC.EXIT_NODATA)
        self.assertTrue(any("reason" in l for l in lines), lines)

    def test_a_reason_passes_and_lands_in_the_notes_file(self):
        code, lines = RC.refuse_if_dirty(
            VERSION, "merged main, regen commit pending", root=self.tmp)
        self.assertIsNone(code, lines)
        notes_path = RC.notes_extra_path(VERSION, root=self.tmp)
        self.assertTrue(os.path.isfile(notes_path))
        with open(notes_path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("merged main, regen commit pending", body)
        self.assertIn("--allow-dirty", body)

    def test_a_second_reason_is_appended_not_overwritten(self):
        RC.record_allow_dirty_reason(VERSION, "first reason", root=self.tmp)
        RC.record_allow_dirty_reason(VERSION, "second reason", root=self.tmp)
        with open(RC.notes_extra_path(VERSION, root=self.tmp),
                 encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("first reason", body)
        self.assertIn("second reason", body)


class StageReleaseFilesUsesRealGit(unittest.TestCase):
    def setUp(self):
        self.tmp = _make_repo()
        for rel in RC.release_paths(VERSION):
            full = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write("stub\n")

    def test_both_files_are_staged(self):
        lines = RC.stage_release_files(VERSION, root=self.tmp)
        self.assertTrue(any("staged" in l for l in lines), lines)
        status = _status(self.tmp)
        for rel in RC.release_paths(VERSION):
            found = [l for l in status.splitlines() if rel in l]
            self.assertTrue(found, status)
            self.assertEqual(found[0][0], "A", status)


class MainRunsDirtyCheckThenRegenerateThenStageThenCheck(unittest.TestCase):
    """The order goal: a clean tree writes, stages, then checks; the check
    step itself is faked here (it is already proven by test_export_public.py
    and test_reproduce_export.py) so this test is only about the new
    ordering and the real staging in between."""

    def setUp(self):
        self.tmp = _make_repo()

    def _bind(self, func):
        def _wrapped(*args, **kwargs):
            kwargs.setdefault("root", self.tmp)
            return func(*args, **kwargs)
        return _wrapped

    def test_a_clean_tree_writes_stages_and_reads_clear(self):
        real_refuse = RC.refuse_if_dirty
        real_stage = RC.stage_release_files

        def fake_regenerate(version):
            for rel in RC.release_paths(version):
                full = os.path.join(self.tmp, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write("stub\n")
            return True, ["regenerated (stub)"]

        def fake_check(version):
            return RC.EXIT_CLEAR, ["CLEAR: stub"]

        with mock.patch.object(RC, "refuse_if_dirty",
                               self._bind(real_refuse)), \
             mock.patch.object(RC, "stage_release_files",
                               self._bind(real_stage)), \
             mock.patch.object(RC, "regenerate", fake_regenerate), \
             mock.patch.object(RC, "check", fake_check):
            code = RC.main(["--version", VERSION])
        self.assertEqual(code, RC.EXIT_CLEAR)
        status = _status(self.tmp)
        for rel in RC.release_paths(VERSION):
            found = [l for l in status.splitlines() if rel in l]
            self.assertTrue(found, status)
            self.assertEqual(found[0][0], "A", status)

    def test_a_dirty_tree_never_reaches_regenerate_or_check(self):
        with open(os.path.join(self.tmp, "scratch.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("uncommitted\n")
        real_refuse = RC.refuse_if_dirty
        regenerate_called = []
        check_called = []

        def fake_regenerate(version):
            regenerate_called.append(version)
            return True, []

        def fake_check(version):
            check_called.append(version)
            return RC.EXIT_CLEAR, []

        with mock.patch.object(RC, "refuse_if_dirty",
                               self._bind(real_refuse)), \
             mock.patch.object(RC, "regenerate", fake_regenerate), \
             mock.patch.object(RC, "check", fake_check):
            code = RC.main(["--version", VERSION])
        self.assertEqual(code, RC.EXIT_NODATA)
        self.assertEqual(regenerate_called, [])
        self.assertEqual(check_called, [])


if __name__ == "__main__":
    unittest.main()
