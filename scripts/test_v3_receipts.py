"""Calibration for scripts/v3_receipts.py.

The one behaviour this file exists to prove: a unit with no genuinely surfaced lesson gets
NO receipt at all, not an empty one. bm_recurrence.py's own contract would already exclude
such a unit from the denominator, but v3_receipts.py refuses to write the row in the first
place, before ever invoking bm_recurrence's CLI. These tests run the REAL bm_recurrence.py
via subprocess (never a stub), because the point is proving the two tools actually
interoperate through the CLI contract, not proving a mock does.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v3_receipts as V  # noqa: E402


class RefusesWithNoSurfacedLesson(unittest.TestCase):
    """The core discipline: no lesson surfaced means no receipt, not a no-op receipt."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        os.unlink(self.db)  # the file must not exist yet; record_unit should never create it

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean if refused before writing

    def test_a_unit_with_empty_surfaced_is_REFUSED(self):
        unit = {'unit_id': 'u-empty', 'surfaced': [], 'applied': [], 'declined': [],
                'before_first_write': False}
        with self.assertRaises(V.NoApplicableLesson):
            V.record_unit(unit, self.db)

    def test_a_refused_unit_writes_no_row_at_all(self):
        """Not even an empty one: the db file itself must not come into existence."""
        unit = {'unit_id': 'u-empty', 'surfaced': [], 'applied': [], 'declined': [],
                'before_first_write': False}
        with self.assertRaises(V.NoApplicableLesson):
            V.record_unit(unit, self.db)
        self.assertFalse(os.path.exists(self.db),
                          'a refused unit must not create the store at all')

    def test_a_unit_missing_the_surfaced_key_entirely_is_also_REFUSED(self):
        unit = {'unit_id': 'u-no-key', 'before_first_write': False}
        with self.assertRaises(V.NoApplicableLesson):
            V.record_unit(unit, self.db)


class RecordsAGenuinelySurfacedLesson(unittest.TestCase):
    """The positive case, proven through the real bm_recurrence.py CLI, not a stub."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        os.unlink(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean if the test never wrote

    def test_a_unit_with_a_surfaced_lesson_records_and_reads_back(self):
        unit = {'unit_id': 'u-real', 'surfaced': ['some-lesson'], 'applied': ['some-lesson'],
                'declined': [], 'reason': '', 'before_first_write': False,
                'worker': 'test-worker'}
        out = V.record_unit(unit, self.db)
        self.assertIn('receipt recorded for u-real', out)
        rep = V.report(self.db)
        self.assertIn('NO-DATA: 1 applicable work unit', rep)

    def test_bm_recurrence_still_refuses_an_id_never_surfaced(self):
        """v3_receipts.py adds a refusal, it does not weaken bm_recurrence's own contract:
        applying an id absent from surfaced must still fail, through the CLI, same as
        calling bm_recurrence.record_receipt directly would."""
        unit = {'unit_id': 'u-bad', 'surfaced': ['a'], 'applied': ['b'], 'declined': [],
                'reason': '', 'before_first_write': False}
        with self.assertRaises(RuntimeError):
            V.record_unit(unit, self.db)


class TheEstateStoreGuard(unittest.TestCase):
    """v3_receipts.py must never point itself at the live .brothermode store."""

    def test_a_dot_brothermode_path_is_flagged(self):
        self.assertTrue(V._refuse_estate_db('/anywhere/.brothermode/recurrence.sqlite3'))

    def test_an_ordinary_scratch_path_is_not_flagged(self):
        self.assertFalse(V._refuse_estate_db('/tmp/some-scratch-dir/receipts.sqlite3'))

    def test_main_exits_2_and_writes_nothing_for_an_estate_path(self):
        code = V.main(['--db', '/tmp/v3-receipts-guard-check/.brothermode/recurrence.sqlite3'])
        self.assertEqual(code, 2)
        self.assertFalse(Path('/tmp/v3-receipts-guard-check').exists())


class TheThreeRealUnitsReachAnHonestDenominator(unittest.TestCase):
    """Integration proof for the actual V3 judgement this file ships: three real token-shield
    units, three genuinely surfaced lessons, denominator below bm_recurrence's own
    MIN_DENOMINATOR, so the report reads NO-DATA rather than a percentage. NO-DATA here is
    the correct, honest verdict, not a failure of this test."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        os.unlink(self.db)

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)  # sbe: allow-silent nothing to clean if the test never wrote

    def test_all_three_units_have_a_surfaced_lesson_none_are_refused(self):
        for unit in V.UNITS:
            self.assertTrue(unit['surfaced'], '%s must carry a genuinely surfaced lesson, '
                             'or it belongs out of UNITS entirely' % unit['unit_id'])

    def test_recording_all_three_yields_denominator_3_no_percentage(self):
        for unit in V.UNITS:
            V.record_unit(unit, self.db)
        rep = V.report(self.db)
        self.assertIn('denominator=3', rep)
        self.assertIn('NO-DATA', rep)
        self.assertNotIn('pre-action memory application rate:', rep)

    def test_main_runs_the_whole_batch_and_exits_0(self):
        code = V.main(['--db', self.db])
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
