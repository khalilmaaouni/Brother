#!/usr/bin/env python3
"""Calibration for tools/bm_vault_ids.py, benchmark row D05.

The property under test is not that an id can be generated. It is that identity
SURVIVES A RENAME, because that is the whole difference between an id and a
filename, and a vault of 802 notes had none.

Two cases here are guards rather than features, and they matter more than the
happy path: resolution must NOT match a filename unless explicitly asked, and a
duplicate id must be reported rather than resolved. Both are the shape that
produced three false passes in this estate's own benchmark on 2026-08-29, where
a check answered "the bad thing is absent" and reported "the good thing is
present".

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_ids as ids  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

NOTE = """---
type: failure
status: standing
created: 2026-08-29
---

# a note about something

Body text that must survive untouched.
"""

NO_FRONTMATTER = "# just a heading\n\nno frontmatter block at all\n"


class MintingIsSafe(unittest.TestCase):
    def test_an_id_matches_the_declared_shape(self):
        self.assertRegex(ids.mint(), r"^n-[0-9a-f]{16}$")

    def test_two_mints_differ(self):
        self.assertNotEqual(ids.mint(), ids.mint())

    def test_mint_never_returns_an_id_already_in_use(self):
        """A duplicate id resolves the WRONG note and says nothing, which is the
        one failure this module must not be able to produce."""
        taken = {ids.mint() for _ in range(50)}
        for _ in range(200):
            self.assertNotIn(ids.mint(taken), taken)

    def test_an_id_carries_no_date_title_or_path(self):
        """An id that means something is an id somebody eventually derives
        instead of looking up."""
        got = ids.mint()
        self.assertNotIn("2026", got)
        self.assertNotIn("/", got)


class ReadingAndWriting(unittest.TestCase):
    def test_a_note_without_an_id_reads_as_None(self):
        self.assertIsNone(ids.read_id(NOTE))

    def test_an_id_survives_a_write_then_read(self):
        nid = ids.mint()
        self.assertEqual(ids.read_id(ids.add_id(NOTE, nid)), nid)

    def test_adding_an_id_changes_NOTHING_else(self):
        """Byte level, because a frontmatter rewrite that reorders keys hides
        whatever else changed inside a diff nobody can read."""
        out = ids.add_id(NOTE, ids.mint())
        for line in ("type: failure", "status: standing", "created: 2026-08-29",
                     "# a note about something", "Body text that must survive untouched."):
            self.assertIn(line, out)
        self.assertEqual(out.count("---"), NOTE.count("---"))

    def test_add_id_is_idempotent(self):
        once = ids.add_id(NOTE, ids.mint())
        self.assertEqual(ids.add_id(once, ids.mint()), once)

    def test_a_note_with_no_frontmatter_is_left_alone(self):
        self.assertEqual(ids.add_id(NO_FRONTMATTER, ids.mint()), NO_FRONTMATTER)

    def test_a_malformed_id_value_reads_as_absent_not_as_a_crash(self):
        broken = NOTE.replace("type: failure", "id: not-a-real-id\ntype: failure")
        self.assertIsNone(ids.read_id(broken))


class IdentitySurvivesARename(unittest.TestCase):
    """The whole point of D05, and the only case that distinguishes an id from
    a filename."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-ids-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        self.original = os.path.join(self.vault, "40-Failures", "the-original-name.md")
        with open(self.original, "w", encoding="utf-8") as fh:
            fh.write(NOTE)
        ids.cmd_assign(self.vault, True)
        with open(self.original, encoding="utf-8") as fh:
            self.nid = ids.read_id(fh.read())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_note_got_an_id(self):
        self.assertIsNotNone(self.nid)

    def test_the_id_still_resolves_after_a_rename(self):
        renamed = os.path.join(self.vault, "40-Failures", "a-completely-different-name.md")
        os.rename(self.original, renamed)
        self.assertEqual(ids.resolve(self.vault, self.nid),
                         os.path.join("40-Failures", "a-completely-different-name.md"))

    def test_the_id_still_resolves_after_a_move_to_another_folder(self):
        os.makedirs(os.path.join(self.vault, "50-Reference"))
        moved = os.path.join(self.vault, "50-Reference", "moved-and-renamed.md")
        os.rename(self.original, moved)
        self.assertEqual(ids.resolve(self.vault, self.nid),
                         os.path.join("50-Reference", "moved-and-renamed.md"))

    def test_the_old_filename_stops_resolving_which_is_the_defect_D05_names(self):
        os.rename(self.original, os.path.join(self.vault, "40-Failures", "new-name.md"))
        self.assertIsNone(ids.resolve(self.vault, "the-original-name", allow_stem=True))


class ResolutionDoesNotGuess(unittest.TestCase):
    """Guards. A resolver that quietly matched a filename would return the right
    answer for every note in a vault where NO id exists, so D05 would measure as
    met while nothing had been built."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-ids-guard-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        with open(os.path.join(self.vault, "some-note.md"), "w", encoding="utf-8") as fh:
            fh.write(NOTE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_unknown_id_does_NOT_match_a_filename_by_default(self):
        self.assertIsNone(ids.resolve(self.vault, "some-note"))

    def test_filename_matching_works_when_explicitly_asked_for(self):
        """Calibration for the case above: the behaviour exists and is
        reachable, so default-off is a choice rather than a missing feature."""
        self.assertEqual(ids.resolve(self.vault, "some-note", allow_stem=True), "some-note.md")

    def test_a_duplicate_id_is_reported_rather_than_resolved(self):
        nid = ids.mint()
        for name in ("one.md", "two.md"):
            with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
                fh.write(ids.add_id(NOTE, nid))
        _by_id, _missing, dupes = ids.index(self.vault)
        self.assertIn(nid, dupes)
        self.assertEqual(sorted(dupes[nid]), ["one.md", "two.md"])


class TheAssignCommandIsSafe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-ids-assign-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.path = os.path.join(self.vault, "note.md")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(NOTE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_dry_run_writes_NOTHING(self):
        """Dry by default, because this edits every file in a corpus and this
        estate has already lost work to a whole-tree rewrite landing on top of
        another writer."""
        with open(self.path, encoding="utf-8") as fh:
            before = fh.read()
        ids.cmd_assign(self.vault, False)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_apply_writes_and_a_second_run_is_a_no_op(self):
        ids.cmd_assign(self.vault, True)
        with open(self.path, encoding="utf-8") as fh:
            after_first = fh.read()
        self.assertIsNotNone(ids.read_id(after_first))
        ids.cmd_assign(self.vault, True)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), after_first)

    def test_check_exits_nonzero_while_notes_lack_an_id(self):
        self.assertEqual(ids.cmd_check(self.vault), 1)
        ids.cmd_assign(self.vault, True)
        self.assertEqual(ids.cmd_check(self.vault), 0)


if __name__ == "__main__":
    unittest.main()
