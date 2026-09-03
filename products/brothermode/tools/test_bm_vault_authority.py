#!/usr/bin/env python3
"""Calibration for tools/bm_vault_authority.py, benchmark row D08.

The property under test is the row's own sentence: a LOWER similarity source of
record outranks a HIGHER similarity casual note. The guard is its shadow: an
unknown authority value must refuse to rank, because silently mapping it low
buries a ruling and mapping it high promotes garbage.

No em or en dashes anywhere in this file.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_authority as auth  # noqa: E402


def note(authority=None):
    lines = ["---", "type: reference", "status: standing"]
    if authority is not None:
        lines.append("authority: %s" % authority)
    lines += ["---", "", "# a note"]
    return "\n".join(lines) + "\n"


class TheRowsOwnSentence(unittest.TestCase):
    def test_a_LOWER_similarity_source_of_record_outranks_a_HIGHER_similarity_casual_note(self):
        self.assertTrue(auth.outranks("source_of_record", 0.31, "casual", 0.97))

    def test_similarity_still_breaks_ties_INSIDE_one_authority_level(self):
        self.assertTrue(auth.outranks("casual", 0.9, "casual", 0.4))
        self.assertFalse(auth.outranks("casual", 0.4, "casual", 0.9))

    def test_derived_sits_strictly_between(self):
        self.assertTrue(auth.outranks("derived", 0.1, "casual", 0.99))
        self.assertTrue(auth.outranks("source_of_record", 0.1, "derived", 0.99))


class ReadingIsHonest(unittest.TestCase):
    def test_absent_reads_as_casual_which_is_what_812_notes_are_today(self):
        self.assertEqual(auth.read_authority(note()), ("casual", None))

    def test_a_declared_level_reads_back(self):
        self.assertEqual(auth.read_authority(note("source_of_record")),
                         ("source_of_record", None))

    def test_an_unknown_value_is_a_finding_not_a_rank(self):
        level, problem = auth.read_authority(note("very_important"))
        self.assertIsNone(level)
        self.assertIn("very_important", problem)


class UnknownRefusesToRank(unittest.TestCase):
    def test_rank_key_raises_rather_than_guessing_a_direction(self):
        with self.assertRaises(ValueError):
            auth.rank_key("very_important", 0.5)
        with self.assertRaises(ValueError):
            auth.rank_key(None, 0.5)


class TheCheckReadsARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-authority-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_typo_is_reported_and_exits_1(self):
        self._write("ok.md", note("derived"))
        self._write("typo.md", note("source-of-record"))
        self.assertEqual(auth.cmd_check(self.vault), 1)

    def test_a_clean_tree_exits_0(self):
        self._write("ok.md", note("derived"))
        self._write("plain.md", note())
        self.assertEqual(auth.cmd_check(self.vault), 0)


if __name__ == "__main__":
    unittest.main()
