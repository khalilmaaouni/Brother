#!/usr/bin/env python3
"""Calibration for tools/bm_vault_asof.py, the D09 as-of query.

The row's own done check, at fixture level: an as-of query at T1 returns the
OLD fact and at T2 returns the NEW one, over a fixture pair linked by
supersedes: with a real interval -- and the interesting case is that the OLD
note never got an explicit valid_to typed on it by hand. This file proves the
derivation (build_effective_windows backfilling valid_to from the successor's
supersedes: edge), not just bm_vault_temporal.py's own per-note in_truth,
which is already covered by that module's own suite.

No em or en dashes anywhere in this file.
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_asof as ba  # noqa: E402

D = datetime.date


class FixtureVault(unittest.TestCase):
    """old.md: valid_from only, NO explicit valid_to. new.md: valid_from later,
    frontmatter supersedes: [[old]]. Nobody ever typed old's valid_to by hand --
    that is exactly the gap this module closes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-asof-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._write("old.md",
                    "---\n"
                    "type: decision\n"
                    "status: standing\n"
                    "valid_from: 2026-01-01\n"
                    "---\n\n# The old decision\nbody\n")
        self._write("new.md",
                    "---\n"
                    "type: decision\n"
                    "status: standing\n"
                    "valid_from: 2026-06-01\n"
                    "supersedes: [[old]]\n"
                    "---\n\n# The new decision\nbody\n")
        self.bt = ba._load("bm_vault_temporal.py", "bm_vault_temporal_for_asof_test")
        self.bg = ba._load("bm_vault_graph.py", "bm_vault_graph_for_asof_test")

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TheDoneCheckItself(FixtureVault):
    """The D09 observable, at fixture level: the same question, two instants,
    two different answers -- and the old note's window closes purely from the
    supersedes: edge, never from its own frontmatter."""

    def test_before_supersession_old_is_the_answer(self):
        rows = {r[0]: r for r in ba.answer_as_of(self.vault, D(2026, 3, 1), self.bt, self.bg)}
        self.assertEqual(rows["old"][1], "declared_true")
        self.assertEqual(rows["new"][1], "declared_false")

    def test_after_supersession_new_is_the_answer_and_old_is_not(self):
        rows = {r[0]: r for r in ba.answer_as_of(self.vault, D(2026, 8, 1), self.bt, self.bg)}
        self.assertEqual(rows["new"][1], "declared_true")
        self.assertEqual(rows["old"][1], "declared_false",
                         "old must stop being the answer once its successor's "
                         "valid_from arrives, even though old.md never had its "
                         "own valid_to typed on it")

    def test_the_answer_provably_differs_between_the_two_instants(self):
        then = {r[0]: r[1] for r in ba.answer_as_of(self.vault, D(2026, 3, 1), self.bt, self.bg)}
        now = {r[0]: r[1] for r in ba.answer_as_of(self.vault, D(2026, 8, 1), self.bt, self.bg)}
        self.assertNotEqual(then["old"], now["old"])

    def test_old_s_derived_valid_to_equals_new_s_valid_from(self):
        windows, _problems, source = ba.build_effective_windows(self.vault, self.bt, self.bg)
        self.assertEqual(windows["old"]["valid_to"], D(2026, 6, 1))
        self.assertEqual(source["old"], "supersedes")

    def test_supersession_day_itself_belongs_to_the_successor(self):
        # valid_to is exclusive (bm_vault_temporal.py's own contract): the day
        # equal to the successor's valid_from is the FIRST day old is no longer
        # the answer.
        rows = {r[0]: r[1] for r in ba.answer_as_of(self.vault, D(2026, 6, 1), self.bt, self.bg)}
        self.assertEqual(rows["old"], "declared_false")
        self.assertEqual(rows["new"], "declared_true")


class ExplicitWindowNeverNarrowedByADerivedOne(FixtureVault):
    """A human who already closed a note's window by hand outranks a later,
    wider derivation: the earlier of the two survives."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-asof-explicit-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        # old.md's own valid_to (2026-04-01) is EARLIER than new.md's valid_from
        # (2026-06-01): the explicit, earlier date must win.
        self._write("old.md",
                    "---\ntype: decision\nstatus: standing\n"
                    "valid_from: 2026-01-01\nvalid_to: 2026-04-01\n---\n\nbody\n")
        self._write("new.md",
                    "---\ntype: decision\nstatus: standing\n"
                    "valid_from: 2026-06-01\nsupersedes: [[old]]\n---\n\nbody\n")
        self.bt = ba._load("bm_vault_temporal.py", "bm_vault_temporal_for_asof_test2")
        self.bg = ba._load("bm_vault_graph.py", "bm_vault_graph_for_asof_test2")

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_earlier_explicit_valid_to_survives(self):
        windows, _problems, source = ba.build_effective_windows(self.vault, self.bt, self.bg)
        self.assertEqual(windows["old"]["valid_to"], D(2026, 4, 1))
        self.assertEqual(source["old"], "own")

    def test_a_gap_note_reads_declared_false_for_both(self):
        # 2026-05-01 sits after old closed and before new opens: neither note
        # is the answer there, and that must be visible rather than papered
        # over by a wrongly widened window.
        rows = {r[0]: r[1] for r in ba.answer_as_of(self.vault, D(2026, 5, 1), self.bt, self.bg)}
        self.assertEqual(rows["old"], "declared_false")
        self.assertEqual(rows["new"], "declared_false")


class TheFourOutcomesAreNeverCollapsed(FixtureVault):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-asof-four-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self._write("timeless.md", "---\ntype: finding\nstatus: standing\n---\n\nbody\n")
        self._write("broken.md",
                    "---\ntype: finding\nstatus: standing\nvalid_from: whenever\n---\n\nbody\n")
        self.bt = ba._load("bm_vault_temporal.py", "bm_vault_temporal_for_asof_test3")
        self.bg = ba._load("bm_vault_graph.py", "bm_vault_graph_for_asof_test3")

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_note_with_no_temporal_field_is_timeless_current_never_a_boolean(self):
        rows = {r[0]: r[1] for r in ba.answer_as_of(self.vault, D(2026, 1, 1), self.bt, self.bg)}
        self.assertEqual(rows["timeless"], "timeless_current")

    def test_a_malformed_window_is_reported_never_coerced(self):
        rows = {r[0]: r[1] for r in ba.answer_as_of(self.vault, D(2026, 1, 1), self.bt, self.bg)}
        self.assertEqual(rows["broken"], "malformed")


class TheCommandLineSurface(FixtureVault):
    def test_cmd_query_reports_no_data_for_an_unknown_stem(self):
        rc = ba.cmd_query(self.vault, D(2026, 3, 1), stem="does-not-exist")
        self.assertEqual(rc, 2)

    def test_cmd_query_exits_0_for_a_known_stem(self):
        rc = ba.cmd_query(self.vault, D(2026, 3, 1), stem="old")
        self.assertEqual(rc, 0)

    def test_cmd_query_exits_0_for_the_whole_vault_summary(self):
        rc = ba.cmd_query(self.vault, D(2026, 3, 1))
        self.assertEqual(rc, 0)

    def test_main_reports_no_data_on_a_missing_vault(self):
        rc = ba.main(["query", "--vault", "/no/such/path", "--date", "2026-01-01"])
        self.assertEqual(rc, 2)

    def test_main_reports_no_data_on_an_unparseable_date(self):
        rc = ba.main(["query", "--vault", self.vault, "--date", "not-a-date"])
        self.assertEqual(rc, 2)

    def test_main_runs_a_real_query_end_to_end(self):
        rc = ba.main(["query", "--vault", self.vault, "--date", "2026-03-01"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
