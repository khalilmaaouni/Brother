#!/usr/bin/env python3
"""Plan E step 2b: a proven diagnosis is brief input, re-admits a parked unit only when its content changes, and starts nothing.

Run: python3 -B scripts/loop/test_diag_notes.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diag_notes as D  # noqa: E402


class Notes(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="diag-notes-", dir=os.path.expanduser("~/.claude/brother-scratch"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_a_new_fact_is_written_and_read_back_for_its_own_sub_unit_only(self):
        self.assertEqual(D.write_note("U", "U.1", "VERIFIED FACT: one", notes=self.d), "written")
        self.assertEqual(D.write_note("U", "U.2", "VERIFIED FACT: two", notes=self.d), "written")
        self.assertEqual(D.note_for("U", "U.1", notes=self.d), "VERIFIED FACT: one")
        self.assertEqual(D.note_for("U", "U.2", notes=self.d), "VERIFIED FACT: two")
        self.assertEqual(D.note_for("U", "U.3", notes=self.d), "")

    def test_an_identical_fact_rewrites_nothing_so_it_readmits_nothing(self):
        D.write_note("U", "U.1", "VERIFIED FACT: one", notes=self.d)
        path = D.note_path("U", notes=self.d)
        before = os.path.getmtime(path); time.sleep(1.1)
        self.assertEqual(D.write_note("U", "U.1", "VERIFIED  FACT:   one", notes=self.d), "unchanged")
        self.assertEqual(os.path.getmtime(path), before, "an unchanged fact must not move the time the pool reads")

    def test_a_changed_fact_replaces_the_old_line(self):
        D.write_note("U", "U.1", "VERIFIED FACT: one", notes=self.d)
        self.assertEqual(D.write_note("U", "U.1", "VERIFIED FACT: other", notes=self.d), "written")
        self.assertEqual(open(D.note_path("U", notes=self.d)).read(), "U.1: VERIFIED FACT: other\n")

    def test_hostile_input_is_refused(self):
        for unit, sub, text in (("", "U.1", "x"), ("U", "", "x"), ("U", "U.1", ""), ("../U", "U.1", "x"), (None, "U.1", "x"), ("U", "U.1", 3)):
            with self.assertRaises(ValueError):
                D.write_note(unit, sub, text, notes=self.d)

    def test_a_missing_note_reads_empty(self):
        self.assertEqual(D.note_for("NONE", "NONE.1", notes=self.d), "")


class Wiring(unittest.TestCase):
    def test_diag_apply_never_starts_a_runner(self):
        src = open(os.path.join(HERE, "diag_apply.py"), encoding="utf-8").read()
        self.assertNotIn("Popen", src)
        self.assertNotIn("unit_runner.py", src)

    def test_the_runner_reads_the_note_into_its_brief_without_resetting_history(self):
        src = open(os.path.join(HERE, "unit_runner.py"), encoding="utf-8").read()
        self.assertIn("_DN.note_for(unit, sub)", src)
        self.assertNotIn("note_for(unit, sub): _fact", src)

    def test_the_pool_counts_the_note_as_a_fact(self):
        import runner_pool as RP
        d = tempfile.mkdtemp(prefix="diag-fact-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        try:
            orig = D.NOTES; D.NOTES = d
            D.write_note("ZZ", "ZZ.1", "VERIFIED FACT: x")
            at = os.path.getmtime(D.note_path("ZZ"))
            self.assertGreaterEqual(RP.fact_time("ZZ", specs=d, bin_dir=d), at)
        finally:
            D.NOTES = orig; shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
