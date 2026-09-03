#!/usr/bin/env python3
"""Calibration for tools/bm_vault_temporal.py, benchmark row D09.

The property under test is the one D09 names: a fact can be asked what it was
at a past instant, and the answer provably differs from its current value. The
guard cases matter as much: a malformed window must be reported and never read
as current, and a note with no window must come back CANNOT SAY rather than
either boolean, because both coercions manufacture false history.

No em or en dashes anywhere in this file.
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_temporal as bt  # noqa: E402

D = datetime.date


def note(**fields):
    lines = ["---", "type: failure", "status: standing"]
    lines += ["%s: %s" % (k, v) for k, v in fields.items()]
    lines += ["---", "", "# a note", "body"]
    return "\n".join(lines) + "\n"


class ParsingIsHonest(unittest.TestCase):
    def test_all_five_fields_read(self):
        w, p = bt.parse(note(valid_from="2026-01-01", valid_to="2026-06-01",
                             observed_at="2026-01-02", ingested_at="2026-01-03",
                             verified_at="2026-01-04"))
        self.assertEqual(p, [])
        self.assertEqual(sorted(w), sorted(bt.FIELDS))

    def test_an_unmigrated_note_is_empty_not_an_error(self):
        """802 real notes look like this today; treating them as broken would
        drown the check in noise and hide the real defects."""
        self.assertEqual(bt.parse(note()), ({}, []))

    def test_an_unparseable_date_is_a_named_problem(self):
        w, p = bt.parse(note(valid_from="soonish"))
        self.assertNotIn("valid_from", w)
        self.assertEqual(p[0][0], "valid_from")

    def test_an_inverted_window_is_a_named_problem(self):
        _w, p = bt.parse(note(valid_from="2026-06-01", valid_to="2026-01-01"))
        self.assertTrue(any("inverted" in reason for _f, reason in p))


class TheTwoClocksActuallyAnswer(unittest.TestCase):
    """The D09 observable: the same fact, two different answers at two instants."""

    def setUp(self):
        self.w, self.p = bt.parse(note(valid_from="2026-03-01", valid_to="2026-06-01"))

    def test_before_the_window_it_was_not_yet_true(self):
        self.assertIs(bt.in_truth(self.w, self.p, D(2026, 2, 28)), False)

    def test_inside_the_window_it_was_true(self):
        self.assertIs(bt.in_truth(self.w, self.p, D(2026, 4, 15)), True)

    def test_valid_from_is_inclusive(self):
        self.assertIs(bt.in_truth(self.w, self.p, D(2026, 3, 1)), True)

    def test_valid_to_is_exclusive_so_supersession_day_belongs_to_the_successor(self):
        self.assertIs(bt.in_truth(self.w, self.p, D(2026, 6, 1)), False)

    def test_the_answer_provably_differs_between_then_and_now(self):
        """The row's own done check, in one assertion."""
        then = bt.in_truth(self.w, self.p, D(2026, 4, 15))
        now = bt.in_truth(self.w, self.p, D(2026, 8, 29))
        self.assertNotEqual(then, now)

    def test_no_valid_to_means_still_true(self):
        w, p = bt.parse(note(valid_from="2026-03-01"))
        self.assertIs(bt.in_truth(w, p, D(2030, 1, 1)), True)


class CannotSayIsNeverCoerced(unittest.TestCase):
    def test_a_malformed_window_is_None_never_current(self):
        """Malformed read as current hides corruption behind a working lookup,
        the duplicate-id failure in different clothes."""
        w, p = bt.parse(note(valid_from="2026-06-01", valid_to="2026-01-01"))
        self.assertIsNone(bt.in_truth(w, p, D(2026, 3, 1)))

    def test_a_missing_window_is_None_never_a_boolean(self):
        w, p = bt.parse(note())
        self.assertIsNone(bt.in_truth(w, p, D(2026, 3, 1)))


class TheCommandsReadARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-temporal-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._write("superseded.md", note(valid_from="2026-01-01", valid_to="2026-06-01"))
        self._write("current.md", note(valid_from="2026-01-01"))
        self._write("unmigrated.md", note())
        self._write("broken.md", note(valid_from="whenever"))

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_reports_the_malformed_note_and_exits_1(self):
        self.assertEqual(bt.cmd_check(self.vault), 1)

    def test_check_exits_0_once_the_malformed_note_is_fixed(self):
        self._write("broken.md", note(valid_from="2026-01-01"))
        self.assertEqual(bt.cmd_check(self.vault), 0)

    def test_asof_finds_the_note_whose_truth_membership_changed(self):
        rows = bt.scan(self.vault)
        by_name = {rel: (w, p) for rel, w, p in rows}
        w, p = by_name["superseded.md"]
        self.assertIs(bt.in_truth(w, p, D(2026, 3, 1)), True)
        self.assertIs(bt.in_truth(w, p, datetime.date.today()), False)


if __name__ == "__main__":
    unittest.main()
