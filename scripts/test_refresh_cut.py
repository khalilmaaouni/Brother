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


def _isolated_git_env():
    """Env for git commands against throwaway fixture repos: masks this
    machine's global and system git config (e.g. tag.gpgsign/gpg.format set
    for the real signed release) so a fixture commit never blocks on
    signing that has nothing to do with the test. Pattern copied from
    test_reproduce_export.py's own _isolated_git_env()."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(args, cwd, check=True):
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, env=_isolated_git_env())
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


def _note_text(version, rev, digest, pr_lines):
    """A release note text carrying the two self-naming fields
    reproduce_export.compare_release_note masks (the source revision stamp
    and the manifest digest) plus a pull request list line, shaped closely
    enough to release_note_from_tree.py's real output for the two regexes
    in reproduce_export.py (NOTE_STAMP_LINE_RE, NOTE_DIGEST_MASK_RE) to
    match it."""
    lines = [
        "# Brother %s" % version,
        "",
        "## Source revision",
        "",
        "Cut from hub commit `%s` (hub, private)." % rev,
        "",
        "Export manifest digest `%s` over 3 exported file(s)." % digest,
        "",
        "## Changelog",
        "",
    ]
    lines.extend(pr_lines)
    lines.append("")
    return "\n".join(lines)


def _write_note(tmp, version, rev, digest, pr_lines):
    path = os.path.join(tmp, "docs", "releases", "%s.md" % version)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_note_text(version, rev, digest, pr_lines))
    return path


class TheNotesContentCannotMoveSinceTheCutCommit(unittest.TestCase):
    """Row measured 2026-09-07: the published v1.0.9 tag's note differs
    from the note reproduce_export.py regenerates at its named source
    revision by one pull request list line, beyond the two self-naming
    fields reproduce_export.compare_release_note already masks. Root
    cause: the note was regenerated on a HEAD that already carried a merge
    the cut commit's branch did not, so the refresh changed the note's
    content, not only its stamp. refuse_if_note_moved must catch that
    before the note and manifest are staged."""

    def setUp(self):
        self.tmp = _make_repo()
        # The note as it stood at the cut commit (HEAD), committed.
        _write_note(self.tmp, VERSION, rev="a" * 40, digest="b" * 64,
                   pr_lines=["- #460 wbs/one"])
        _git(["add", "-A"], self.tmp)
        _git(["commit", "-q", "-m", "cut"], self.tmp)

    def test_a_note_that_gained_a_pull_request_line_refuses(self):
        # The regenerated note: stamp and digest free to move, but the
        # pull request list gained a line the committed note never had.
        _write_note(self.tmp, VERSION, rev="c" * 40, digest="d" * 64,
                   pr_lines=["- #460 wbs/one",
                            "- #466 wbs/portability-release"])
        code, lines = RC.refuse_if_note_moved(VERSION, root=self.tmp)
        self.assertEqual(code, RC.EXIT_REFUSED, lines)
        self.assertTrue(any(
            "the note's content moved since the cut commit" in l
            for l in lines), lines)
        self.assertTrue(any("466" in l for l in lines), lines)

    def test_only_the_stamp_and_digest_moving_clears(self):
        # The regenerated note: same pull request list, only the two
        # fields compare_release_note already exists to mask have moved.
        _write_note(self.tmp, VERSION, rev="c" * 40, digest="d" * 64,
                   pr_lines=["- #460 wbs/one"])
        code, lines = RC.refuse_if_note_moved(VERSION, root=self.tmp)
        self.assertIsNone(code, lines)

    def test_a_first_cut_with_no_committed_note_is_not_a_refusal(self):
        other_version = "8.8.8"
        _write_note(self.tmp, other_version, rev="c" * 40, digest="d" * 64,
                   pr_lines=["- #460 wbs/one"])
        code, lines = RC.refuse_if_note_moved(other_version, root=self.tmp)
        self.assertIsNone(code, lines)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
