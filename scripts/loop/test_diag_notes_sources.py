#!/usr/bin/env python3
"""CT1.a: diag_notes keeps one fact per source for a sub unit.

Run: python3 -B scripts/loop/test_diag_notes_sources.py
"""
import math
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diag_notes as D  # noqa: E402


class _SourceNotes(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="diag-notes-src-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_landing_note_and_a_diagnosis_note_both_survive(self):
        self.assertEqual(D.write_note("U", "U.1", "LANDING REFUSAL 1: bad", notes=self.d), "written")
        self.assertEqual(D.write_note("U", "U.1", "VERIFIED FACT: good", notes=self.d), "written")
        text = open(D.note_path("U", notes=self.d), encoding="utf-8").read()
        self.assertIn("LANDING REFUSAL 1: bad", text)
        self.assertIn("VERIFIED FACT: good", text)
        self.assertEqual(D.note_for("U", "U.1", notes=self.d),
                         "LANDING REFUSAL 1: bad\nVERIFIED FACT: good")

    def test_the_same_source_replaces_only_its_own_line(self):
        D.write_note("U", "U.1", "LANDING REFUSAL 1: a", notes=self.d)
        D.write_note("U", "U.1", "VERIFIED FACT: b", notes=self.d)
        self.assertEqual(D.write_note("U", "U.1", "LANDING REFUSAL 2: c", notes=self.d), "written")
        text = open(D.note_path("U", notes=self.d), encoding="utf-8").read()
        self.assertEqual(text.count("LANDING REFUSAL"), 1)
        self.assertIn("VERIFIED FACT: b", text)
        self.assertEqual(D.note_for("U", "U.1", notes=self.d),
                         "LANDING REFUSAL 2: c\nVERIFIED FACT: b")

    def test_an_identical_fact_does_not_move_the_modification_time(self):
        D.write_note("U", "U.1", "VERIFIED FACT: one", notes=self.d)
        path = D.note_path("U", notes=self.d)
        before = os.path.getmtime(path)
        time.sleep(1.1)
        self.assertEqual(D.write_note("U", "U.1", "VERIFIED  FACT:   one", notes=self.d), "unchanged")
        self.assertEqual(os.path.getmtime(path), before)

    def test_the_landing_fact_leads_and_the_rest_are_newest_first(self):
        D.write_note("U", "U.1", "VERIFIED FACT: old", notes=self.d)
        D.write_note("U", "U.1", "session note", notes=self.d)
        D.write_note("U", "U.1", "LANDING REFUSAL: landed", notes=self.d)
        self.assertEqual(D.note_for("U", "U.1", notes=self.d),
                         "LANDING REFUSAL: landed\nsession note\nVERIFIED FACT: old")

    def test_source_of_reads_the_three_prefixes_and_refuses_non_text(self):
        self.assertEqual(D.source_of("LANDING REFUSAL x"), "landing")
        self.assertEqual(D.source_of("  VERIFIED   FACT: y"), "diagnosis")
        self.assertEqual(D.source_of("anything else"), "session")
        for bad in (None, 0, True, -1, math.nan, b"x", [], {}, ()):
            with self.assertRaises(ValueError):
                D.source_of(bad)

    def test_a_legacy_one_line_note_reads_back_and_u1_never_reads_u10(self):
        with open(os.path.join(self.d, "U.txt"), "w", encoding="utf-8") as fh:
            fh.write("U.1: VERIFIED FACT: one\n")
            fh.write("U.10: VERIFIED FACT: ten\n")
        self.assertEqual(D.note_for("U", "U.1", notes=self.d), "VERIFIED FACT: one")
        self.assertEqual(D.note_for("U", "U.10", notes=self.d), "VERIFIED FACT: ten")

    def test_hostile_input_is_refused(self):
        for bad in (None, 0, True, -1, math.nan, b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                D.source_of(bad)
            with self.assertRaises(ValueError):
                D.write_note(bad, bad, bad, notes=self.d)
            with self.assertRaises(ValueError):
                D.note_path(bad, notes=self.d)
            self.assertEqual(D.note_for(bad, bad, notes=self.d), "")
        with self.assertRaises(ValueError):
            D.write_note("", "", "", notes=self.d)
        with self.assertRaises(ValueError):
            D.note_path("")
        with self.assertRaises(ValueError):
            D.note_path("U", notes=object())
        self.assertEqual(D.note_for("", "", notes=self.d), "")


if __name__ == "__main__":
    unittest.main()
